"""Bounded event-time windows, recomputed on arrivals, partitioned by device."""
import math
from datetime import datetime, timezone, timedelta
from itertools import groupby

ZONE = timezone(timedelta(hours=8))


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("time需为时间字符串")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(value, "%m/%d/%Y %H:%M:%S")
    return (dt if dt.tzinfo else dt.replace(tzinfo=ZONE)).timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("数值字段有空值或非数值，请先过滤或补齐")
    return value


def merge_window(previous, incoming, minutes):
    points = {(row["deviceId"], row["time"]): row for row in previous}
    watermark = max((timestamp(row["time"]) for row in previous), default=float('-inf'))
    duplicates = 0
    for row in incoming:
        time = iso(timestamp(row["time"]))
        point = {"time": time, "deviceId": row["deviceId"], "value": row["value"]}
        key = (point["deviceId"], time)
        duplicates += int(points.get(key) == point)
        points[key] = point
        watermark = max(watermark, timestamp(time))
    cutoff = watermark - minutes * 60
    rows = [row for row in points.values() if timestamp(row["time"]) >= cutoff]
    if len(rows) > 5000:
        raise ValueError("事件窗口超过5,000个数据点，请缩短窗口或减少设备范围")
    rows.sort(key=lambda row: (row["deviceId"], row["time"]))
    return rows, duplicates, len(points) - len(rows), iso(watermark) if rows else None


def ordered(config):
    by_id = {node['id']: node for node in config['nodes']}
    current = next(node for node in config['nodes'] if node['kind'] == 'source')
    result = []
    while current:
        result.append(current)
        target = next((edge['to'] for edge in config['edges'] if edge['from'] == current['id']), None)
        current = by_id[target] if target else None
    return result


def calculate(config, inputs):
    rows = [dict(row) for row in inputs]
    trace = []
    for node in ordered(config):
        before = len(rows)
        kind, field = node['kind'], node['field']
        if kind == 'filter':
            op = node['compare']
            threshold = float(node['value']) if op not in ('exists', 'missing') else 0
            def keep(row):
                value = row.get(field)
                if op == 'exists': return value is not None
                if op == 'missing': return value is None
                if value is None: return False
                value = number(value)
                return {'eq': value == threshold, 'ne': value != threshold, 'gt': value > threshold,
                        'gte': value >= threshold, 'lt': value < threshold, 'lte': value <= threshold}[op]
            rows = [row for row in rows if keep(row)]
        elif kind == 'fill':
            rows = [{**row, field: float(node['value']) if row.get(field) is None else row[field]} for row in rows]
        elif kind in ('aggregate', 'align', 'constant', 'extreme'):
            output = []
            for device, group in groupby(sorted(rows, key=lambda row: (row['deviceId'], row['time'])), lambda row: row['deviceId']):
                records = list(group)
                if kind in ('aggregate', 'align'):
                    width = node['interval'] * 60
                    buckets = {}
                    for row in records:
                        key = math.floor(timestamp(row['time']) / width) * width
                        values = buckets.setdefault(key, [])
                        if row.get(field) is not None: values.append(number(row[field]))
                    keys = sorted(buckets)
                    if kind == 'align' and keys:
                        count = round((keys[-1] - keys[0]) / width) + 1
                        if count + len(output) > 5000: raise ValueError('频率对齐超过5,000个时间桶，请增大间隔')
                        for index in range(count): buckets.setdefault(keys[0] + index * width, [])
                    for time, values in sorted(buckets.items()):
                        output.append({'deviceId': device, 'time': iso(time), field: math.fsum(values) / len(values) if values else None, 'count': len(values)})
                elif kind == 'constant':
                    previous, count = None, 0
                    for row in records:
                        value = row.get(field)
                        if value is None: previous, count = None, 0
                        else:
                            value = number(value)
                            count = count + 1 if value == previous else 1
                            previous = value
                        output.append({**row, 'constant': count >= int(node['value']), 'consecutive': count})
                else:
                    values = [number(row[field]) for row in records if row.get(field) is not None]
                    lo, hi = (min(values), max(values)) if values else (None, None)
                    output.extend({**row, 'extreme': '' if row.get(field) is None else '最小/最大值' if lo == hi else '最小值' if row[field] == lo else '最大值' if row[field] == hi else ''} for row in records)
            rows = output
        if len(rows) > 5000: raise ValueError('处理结果超过5,000条')
        trace.append({'id': node['id'], 'name': node['name'], 'before': before, 'after': len(rows), 'preview': rows[:10]})
    return rows, trace
