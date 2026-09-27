"""Bounded, authenticated application data reads; no browser-supplied URLs."""
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Request
from app.dependencies import CurrentUser, DbSession
from app.services.application_schema import Binding, ApplicationConfig

router=APIRouter(prefix='/studio/applications', tags=['applications'])


async def read_source(binding, db, user, token=None):
    source_time=None; source_status=''; source_name='页面数据'
    if binding.kind=='data_service':
        from app.api.data_services import owned
        item=await owned(binding.id,db,user)
        if not item.config['enabled']: raise HTTPException(409,'绑定的数据服务已停用')
        rows=item.config['rows']; fields=item.config['fields']; source_name=item.name
        source_time=item.config['captured_at']
    elif binding.kind=='realtime':
        from app.api.realtime import owned
        item=await owned(binding.id,db,user)
        rows=item.runtime.get('rows',[]); fields=list(dict.fromkeys(key for row in rows for key in row))
        source_name=item.name; source_time=item.runtime.get('processed_at')
        source_status={'running':'运行中','stopped':'已停止','error':'运行异常'}[item.status]
        if item.status=='error': raise HTTPException(409,'实时分析任务异常，请先检查来源任务')
    elif binding.kind=='platform':
        from app.services.realtime_poll import fetch
        from app.config import settings
        if not settings.PLATFORM_DEVICE_BASE_URL: raise HTTPException(503,'后台未配置设备服务地址')
        if not token: raise HTTPException(400,'读取设备指标需要使用平台账号登录')
        try: rows=await fetch(binding.platform.model_dump(),token)
        except ValueError as error: raise HTTPException(400,str(error))
        except Exception: raise HTTPException(502,'设备数据读取失败，请检查平台服务后重试')
        fields=['deviceId','time','value']; source_name=binding.platform.table
        source_time=max((r['time'] for r in rows),default=None)
    else:
        rows=binding.rows; fields=list(dict.fromkeys(key for row in rows for key in row))
    return {'rows':rows[:1000],'fields':fields,'total':len(rows),'truncated':len(rows)>1000,
            'source_name':source_name,'source_time':source_time,'source_status':source_status,
            'read_at':datetime.now(timezone.utc).isoformat()}


def check_widgets(config, result):
    fields=set(result['fields'])
    def filter_text(value):
        if value is None: return ''
        if isinstance(value,bool): return 'true' if value else 'false'
        return str(value)
    for widget in config.all_widgets():
        required=[widget.filterField] if widget.filterField else []
        if widget.kind=='table': required+=widget.columns
        if widget.kind in ('line','bar'): required+=[widget.field,widget.xField]+([widget.groupField] if widget.groupField else [])
        if widget.kind=='metric' and widget.aggregation!='count': required+=[widget.field]
        if widget.kind=='select': required+=[widget.field]
        if widget.kind in ('equipment','twin'): required+=[widget.field,widget.groupField]
        if widget.action.valueField: required+=[widget.action.valueField]
        if any(field not in fields for field in required):
            raise HTTPException(422,f'“{widget.title}”的绑定字段不存在，请同步数据并重新选择')
        if widget.kind in ('line','bar','metric','equipment','twin') and not (widget.kind=='metric' and widget.aggregation=='count'):
            rows=[r for r in result['rows'] if widget.filterVariable or not widget.filterField or filter_text(r.get(widget.filterField))==widget.filterValue]
            if any(r.get(widget.field) is not None and (isinstance(r[widget.field],bool) or not isinstance(r[widget.field],(int,float))) for r in rows):
                raise HTTPException(422,f'“{widget.title}”需要数值字段，请先治理数据类型')


def apply_logic(config,result):
    if config.mode!='logic': return result
    import operator
    comparisons={'gt':operator.gt,'gte':operator.ge,'lt':operator.lt,'lte':operator.le,'eq':operator.eq,'ne':operator.ne}
    rows=[dict(row) for row in result['rows']];fields=list(result['fields'])
    for rule in config.logic:
        if rule.field not in fields: raise HTTPException(422,'业务逻辑引用了不存在的输入字段')
        if rule.output in fields: raise HTTPException(422,'业务逻辑输出字段与已有字段重复，请更换名称')
        for row in rows:
            value=row.get(rule.field)
            if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float))): raise HTTPException(422,'业务条件需要数值字段，请先处理数据类型')
            row[rule.output]=None if value is None else rule.whenTrue if comparisons[rule.compare](value,rule.value) else rule.whenFalse
        fields.append(rule.output)
    return {**result,'rows':rows,'fields':fields}


@router.post('/data')
async def data(source: Binding, db:DbSession, user:CurrentUser, request:Request):
    return await read_source(source,db,user,request.headers.get('X-Platform-Token'))


@router.post('/debug')
async def debug(config:ApplicationConfig, db:DbSession, user:CurrentUser, request:Request):
    result=apply_logic(config,await read_source(config.source,db,user,request.headers.get('X-Platform-Token')))
    check_widgets(config,result)
    return {**result,'checks':[{'id':w.id,'title':w.title,'status':'passed'} for w in config.all_widgets()]}
