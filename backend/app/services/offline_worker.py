"""One short-lived, read-only SQLite process per offline query.

Input is assembled by the authenticated API; no user-supplied paths or DB URLs.
"""
import hashlib
import json
import math
import sqlite3
import sys
import time
from pathlib import Path

from fastapi import HTTPException
from app.api.datasets import MAX_DATA, read_rows
from app.api.modeling import ModelField, convert, evaluate

MAX_SOURCE_BYTES = 25 * 1024 * 1024
MAX_ROWS = 200000
MAX_RESULT_BYTES = 5 * 1024 * 1024
MAX_STEPS = 5000000
FUNCTIONS = set("abs avg count coalesce round sum total min max ifnull nullif lower upper length substr substring trim ltrim rtrim replace instr like glob date time datetime strftime julianday unixepoch typeof iif json_extract json_valid json_type json_array_length row_number rank dense_rank lag lead first_value last_value percent_rank cume_dist ntile".split())
SQL_TYPES = {"string":"TEXT", "integer":"INTEGER", "number":"REAL", "boolean":"INTEGER", "datetime":"TEXT", "json":"TEXT"}


def quote(value):
    return '"' + value.replace('"', '""') + '"'


def sql_value(value, field):
    value = convert(value, field)
    if value is not None and field.type == "json":
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value


def execute(job):
    started = time.monotonic()
    source_bytes, row_count, source_info = 0, 0, []
    connection = sqlite3.connect(":memory:")
    try:
        connection.enable_load_extension(False)
        connection.execute("PRAGMA temp_store=MEMORY")
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COLUMN, 200)
        # SQLite counts UTF-8 bytes; the API limit is 20,000 Unicode characters.
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 80000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 100)
        connection.setlimit(sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 20)
        connection.setlimit(sqlite3.SQLITE_LIMIT_VDBE_OP, 100000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        tables = set()
        for source in job["sources"]:
            with Path(source["path"]).open("rb") as file:
                content = file.read(MAX_DATA + 1)
            source_bytes += len(content)
            if len(content) > MAX_DATA or source_bytes > MAX_SOURCE_BYTES:
                raise HTTPException(413, "本次查询的源文件合计不能超过25MB，单文件不能超过10MB")
            if hashlib.sha256(content).hexdigest() != source["sha256"]:
                raise HTTPException(409, "源文件内容已变化，请重新上传并绑定模型")
            rows = read_rows(content, source["suffix"])
            row_count += len(rows)
            if row_count > MAX_ROWS:
                raise HTTPException(413, "本次查询的模型输入记录合计不能超过200,000条")
            fields = [ModelField.model_validate(field) for field in source["fields"]]
            validation = evaluate(rows, fields)
            if not validation["ok"]:
                error = validation["errors"][0]
                raise HTTPException(422, f"{source['name']}数据校验失败，第{error['row']}条：{error['message']}")
            table = source["table_name"]
            definitions = ', '.join(quote(field.name) + ' ' + SQL_TYPES[field.type] for field in fields)
            connection.execute(f"CREATE TABLE {quote(table)} ({definitions})")
            connection.executemany(f"INSERT INTO {quote(table)} VALUES ({','.join('?' for _ in fields)})",
                                   (tuple(sql_value(row.get(field.source) if field.source else None, field) for field in fields) for row in rows))
            tables.add(table.lower())
            source_info.append({"id":source["id"], "name":source["name"], "table_name":table,
                                "revision":source["revision"], "row_count":len(rows), "sha256":source["sha256"]})
        connection.commit()
        connection.execute("PRAGMA query_only=ON")
        denied = False

        def authorize(action, arg1, arg2, database, trigger):
            nonlocal denied
            allowed = action == sqlite3.SQLITE_SELECT
            if action == sqlite3.SQLITE_READ:
                # SQLite may omit the database name for COUNT(*) reads.
                allowed = database in ("main", None) and (arg1 or "").lower() in tables
            elif action == sqlite3.SQLITE_FUNCTION:
                allowed = (arg2 or "").lower() in FUNCTIONS
            if not allowed:
                denied = True
            return sqlite3.SQLITE_OK if allowed else sqlite3.SQLITE_DENY

        steps = 0
        def progress():
            nonlocal steps
            steps += 1000
            return int(steps > MAX_STEPS or time.monotonic() - started > 8)

        connection.set_authorizer(authorize)
        connection.set_progress_handler(progress, 1000)
        try:
            cursor = connection.execute(job["sql"])
            if not cursor.description:
                raise HTTPException(400, "请输入返回数据的SELECT查询")
            columns = [column[0] for column in cursor.description]
            rows, byte_count, truncated, large_integers = [], 0, False, set()
            while True:
                row = cursor.fetchone()
                if row is None:
                    break
                if len(rows) >= job["row_limit"]:
                    truncated = True
                    break
                values = []
                for index, value in enumerate(row):
                    if isinstance(value, bytes):
                        raise HTTPException(400, "查询结果不支持二进制字段，请转换为文本")
                    if isinstance(value, float) and not math.isfinite(value):
                        raise HTTPException(400, "计算结果超出有效数值范围，请检查SQL表达式")
                    if isinstance(value, int) and abs(value) > 9007199254740991:
                        large_integers.add(index)
                        value = str(value)
                    values.append(value)
                size = len(json.dumps(values, ensure_ascii=False, allow_nan=False).encode())
                if byte_count + size > MAX_RESULT_BYTES:
                    truncated = True
                    break
                rows.append(values)
                byte_count += size
            return {"columns":columns, "rows":rows, "returned_rows":len(rows), "truncated":truncated,
                    "row_limit":job["row_limit"], "elapsed_ms":round((time.monotonic()-started)*1000),
                    "sources":source_info, "large_integer_columns":sorted(large_integers)}
        except sqlite3.Error as error:
            if denied:
                raise HTTPException(400, "只支持已选模型的只读查询；不支持写入、系统表、递归或该SQL函数")
            if "interrupted" in str(error):
                raise HTTPException(408, "查询超出计算限制，请增加筛选条件或减少关联数据")
            raise HTTPException(400, f"SQL执行失败：{error}")
    except FileNotFoundError:
        raise HTTPException(410, "源文件内容缺失，请重新上传并绑定模型")
    except sqlite3.Error as error:
        raise HTTPException(400, f"模型数据载入失败：{error}")
    except MemoryError:
        raise HTTPException(400, "数据或SQL超出计算限制，请减少字段、数据量或表达式长度")
    finally:
        connection.close()


if __name__ == "__main__":
    try:
        result = {"result":execute(json.load(sys.stdin))}
    except HTTPException as error:
        result = {"status":error.status_code, "detail":error.detail}
    except Exception:
        result = {"status":500, "detail":"查询执行失败，请检查模型配置或联系管理员"}
    sys.stdout.write(json.dumps(result, ensure_ascii=True, allow_nan=False))
