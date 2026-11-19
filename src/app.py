"""S3 -> Lambda -> RDS (MySQL) -> SNS order-file processor.

Triggered when a .csv file lands under incoming/ in the S3 bucket:
  1. Download the file from S3
  2. Validate every row (csv_validator.py)
  3. Upsert valid rows into the `orders` table in RDS over TLS
  4. Email a summary through SNS

Bad rows are skipped and listed in the email. Files that can't be processed at all
(wrong columns, too large, not UTF-8) are rejected with an email and are not retried.
Database or network errors are raised so Lambda retries the event, and after the
retry the event goes to the dead-letter queue.
"""

import json
import logging
import os
import urllib.parse

import boto3
import pymysql

from csv_validator import CsvFormatError, parse_orders

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DB_HOST = os.environ["DB_HOST"]
DB_PORT = int(os.environ.get("DB_PORT", "3306"))
DB_NAME = os.environ["DB_NAME"]
DB_SECRET_ARN = os.environ["DB_SECRET_ARN"]
TOPIC_ARN = os.environ["TOPIC_ARN"]
MAX_FILE_BYTES = int(os.environ.get("MAX_FILE_BYTES", str(5 * 1024 * 1024)))
MAX_ERRORS_IN_EMAIL = 20
CA_BUNDLE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rds-global-bundle.pem")

s3 = boto3.client("s3")
sns = boto3.client("sns")
secrets = boto3.client("secretsmanager")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS orders (
    order_id      VARCHAR(32)   NOT NULL PRIMARY KEY,
    customer_name VARCHAR(100)  NOT NULL,
    product       VARCHAR(100)  NOT NULL,
    quantity      INT           NOT NULL,
    unit_price    DECIMAL(10,2) NOT NULL,
    order_date    DATE          NOT NULL,
    source_file   VARCHAR(1024) NOT NULL,
    loaded_at     TIMESTAMP     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
)
"""

# Re-uploading a file updates rows instead of failing on duplicate keys (idempotent).
UPSERT_SQL = """
INSERT INTO orders (order_id, customer_name, product, quantity, unit_price, order_date, source_file)
VALUES (%s, %s, %s, %s, %s, %s, %s) AS new
ON DUPLICATE KEY UPDATE
    customer_name = new.customer_name,
    product       = new.product,
    quantity      = new.quantity,
    unit_price    = new.unit_price,
    order_date    = new.order_date,
    source_file   = new.source_file
"""

# Reused across warm invocations so each event doesn't open a new DB connection.
_connection = None


def _connect():
    # Read the secret on every new connection: RDS rotates the master password automatically.
    secret = json.loads(secrets.get_secret_value(SecretId=DB_SECRET_ARN)["SecretString"])
    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=secret["username"],
        password=secret["password"],
        database=DB_NAME,
        connect_timeout=5,
        autocommit=False,
        ssl_ca=CA_BUNDLE,          # TLS to RDS, verified against the AWS RDS CA bundle
        ssl_verify_cert=True,
        ssl_verify_identity=True,
    )


def get_connection():
    global _connection
    if _connection is not None:
        try:
            _connection.ping(reconnect=False)
            return _connection
        except pymysql.MySQLError:
            logger.info("Cached DB connection is no longer usable - reconnecting")
            _connection = None
    _connection = _connect()
    return _connection


def notify(subject: str, message: str) -> None:
    sns.publish(TopicArn=TOPIC_ARN, Subject=subject[:99], Message=message)


def lambda_handler(event, context):
    results = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        size = int(record["s3"]["object"].get("size", 0))
        try:
            results.append(process_file(bucket, key, size))
        except Exception as exc:
            logger.exception("Failed to process s3://%s/%s", bucket, key)
            notify(
                f"[pipeline] FAILED - {os.path.basename(key)}",
                f"Processing of s3://{bucket}/{key} failed and will be retried.\n\n"
                f"Error: {type(exc).__name__}: {exc}\n\n"
                "If the retry also fails, the event is kept in the dead-letter queue.",
            )
            raise
    return {"files": results}


def process_file(bucket: str, key: str, size: int) -> dict:
    name = os.path.basename(key)

    if size > MAX_FILE_BYTES:
        return _reject(bucket, key, f"file is {size} bytes; the limit is {MAX_FILE_BYTES} bytes")

    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    try:
        orders, row_errors = parse_orders(body.decode("utf-8-sig"))
    except UnicodeDecodeError:
        return _reject(bucket, key, "file is not UTF-8 text")
    except CsvFormatError as exc:
        return _reject(bucket, key, str(exc))

    conn = get_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
            if orders:
                cur.executemany(
                    UPSERT_SQL,
                    [
                        (o.order_id, o.customer_name, o.product, o.quantity, o.unit_price, o.order_date, key)
                        for o in orders
                    ],
                )
            cur.execute("SELECT COUNT(*) FROM orders")
            (table_rows,) = cur.fetchone()
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except pymysql.MySQLError:
            pass
        raise

    summary = {
        "file": f"s3://{bucket}/{key}",
        "status": "PARTIAL" if row_errors else "OK",
        "rows_loaded": len(orders),
        "rows_rejected": len(row_errors),
        "orders_table_total": table_rows,
    }
    logger.info(json.dumps(summary))

    lines = [
        f"File:            {summary['file']}",
        f"Rows loaded:     {summary['rows_loaded']}",
        f"Rows rejected:   {summary['rows_rejected']}",
        f"Orders in table: {table_rows}",
    ]
    if row_errors:
        lines += ["", "Rejected rows:"] + [f"  - {err}" for err in row_errors[:MAX_ERRORS_IN_EMAIL]]
        if len(row_errors) > MAX_ERRORS_IN_EMAIL:
            lines.append(f"  ... and {len(row_errors) - MAX_ERRORS_IN_EMAIL} more (see CloudWatch Logs)")
    notify(f"[pipeline] {summary['status']} - {name}", "\n".join(lines))
    return summary


def _reject(bucket: str, key: str, reason: str) -> dict:
    logger.warning("Rejected s3://%s/%s: %s", bucket, key, reason)
    notify(
        f"[pipeline] REJECTED - {os.path.basename(key)}",
        f"s3://{bucket}/{key} was not processed.\n\nReason: {reason}\n\n"
        "Fix the file and upload it again.",
    )
    return {"file": f"s3://{bucket}/{key}", "status": "REJECTED", "reason": reason}
