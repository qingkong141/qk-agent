"""Deterministic identity lookup, scoped aliases and registered system information."""
import csv
import io
import json
import uuid
import zipfile
from urllib.parse import quote
from xml.sax.saxutils import escape
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.api.datasets import identity, scope
from app.api.studio import RevisionInput
from app.dependencies import CurrentUser, DbSession
from app.models.master_index import MasterAlias, MasterIndex

router = APIRouter(prefix='/studio/master-index', tags=['master-index'])
RULES = '''# 患者（设备）主索引编码规则

版本：1.0

1. 患者前缀PAT-，设备前缀DEV-，后接服务端UUID v4的32位十六进制大写字符。不包含姓名、身份证、设备序列号等业务含义。
2. 主编码由服务器生成，数据库全局唯一；更名、修改关联信息均不改变编码，患者和设备类型创建后不可互换。
3. 同一账号数据域内，“对象类型＋业务系统标识＋系统内编号”只能关联一个主索引，由数据库唯一约束保证，包括并发提交。
4. 业务系统标识和系统内编号区分大小写；录入时去除首尾空格。一个对象可以在同一系统有多个不同编号。
5. 通过主编码、名称或任一系统内编号检索，返回该对象的全部映射与登记信息。不会凭同名自动合并，也不会模糊匹配患者身份。
6. 系统信息是人工/API登记的快照，不代表实时调用HIS、EMR等系统。接入这些系统时需由授权同步任务更新对应映射内容。
7. 修改采用版本检查，冲突需刷新。删除同时删除映射；删除的编码不会重新分配给其他对象。导出关系表不包含登记信息正文。
8. 不同平台账号或API终端用户相互隔离。真实医院级共享主索引需在部署时明确统一租户和授权范围。
'''


class AliasInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    system: str = Field(min_length=1, max_length=64)
    external_id: str = Field(min_length=1, max_length=160)
    attributes: dict = Field(default_factory=dict)

    @model_validator(mode='after')
    def bounds(self):
        if len(self.attributes)>50 or len(json.dumps(self.attributes,ensure_ascii=False).encode())>20000:
            raise ValueError('单系统登记信息最多50项、20KB')
        if any(ord(c)<32 for c in self.system+self.external_id):
            raise ValueError('系统标识和编号不能包含控制字符')
        return self


class IndexInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=120)
    kind: Literal['patient','device']
    aliases: list[AliasInput] = Field(min_length=1, max_length=30)
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def unique(self):
        if len({(a.system,a.external_id) for a in self.aliases})!=len(self.aliases):
            raise ValueError('同一系统的编号不能重复')
        return self


async def owned(item_id, db, user):
    item=await db.scalar(select(MasterIndex).where(MasterIndex.id==item_id,*scope(MasterIndex,user)))
    if not item: raise HTTPException(404,'主索引不存在')
    return item


async def info(item,db):
    aliases=await db.scalars(select(MasterAlias).where(MasterAlias.master_id==item.id).order_by(MasterAlias.system,MasterAlias.external_id))
    return {'id':item.id,'name':item.name,'kind':item.kind,'code':item.code,'revision':item.revision,
            'aliases':[{'system':a.system,'external_id':a.external_id,'attributes':a.attributes} for a in aliases]}


@router.get('/rules')
async def rules(user: CurrentUser):
    scope(MasterIndex,user)
    return Response(rules_document(), media_type='application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                    headers={'Content-Disposition': "attachment; filename=master-index-rules.docx; filename*=UTF-8''"+quote('主索引编码规则.docx')})


def rules_document():
    """Build a small editable Word document from the same authoritative rules."""
    paragraphs = [('Title', '患者与设备主索引编码规则'),
                  ('Normal', '适用范围：患者及设备主索引的编码、跨系统标识关联、查询与维护。')]
    paragraphs.extend(('Normal', line) for line in RULES.splitlines() if line.strip() and not line.startswith('#'))
    body = ''.join(f'<w:p><w:pPr><w:pStyle w:val="{style}"/></w:pPr><w:r><w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>' for style,text in paragraphs)
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('[Content_Types].xml', '''<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/></Types>''')
        archive.writestr('_rels/.rels', '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>''')
        archive.writestr('word/_rels/document.xml.rels', '''<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>''')
        archive.writestr('word/styles.xml', '''<?xml version="1.0" encoding="UTF-8"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:spacing w:after="160" w:line="320" w:lineRule="auto"/><w:widowControl/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="宋体"/><w:color w:val="000000"/><w:sz w:val="22"/><w:lang w:val="zh-CN" w:eastAsia="zh-CN"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:after="280"/></w:pPr><w:rPr><w:rFonts w:eastAsia="微软雅黑"/><w:b/><w:sz w:val="32"/></w:rPr></w:style>
</w:styles>''')
        archive.writestr('word/document.xml', f'''<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>{body}<w:sectPr><w:pgSz w:w="12240" w:h="15840"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134" w:header="567" w:footer="567" w:gutter="0"/></w:sectPr></w:body></w:document>''')
    return output.getvalue()


@router.get('/export')
async def export(db: DbSession, user: CurrentUser):
    rows=await db.execute(select(MasterIndex,MasterAlias).join(MasterAlias,MasterAlias.master_id==MasterIndex.id).where(*scope(MasterIndex,user)).order_by(MasterIndex.code,MasterAlias.system))
    out=io.StringIO();writer=csv.writer(out);writer.writerow(['主编码','类型','名称','业务系统','系统内编号'])
    def safe(value): return "'"+value if value.lstrip().startswith(('=','+','-','@')) else value
    for item,alias in rows: writer.writerow([safe(v) for v in [item.code,'患者' if item.kind=='patient' else '设备',item.name,alias.system,alias.external_id]])
    return Response('\ufeff'+out.getvalue(),media_type='text/csv',headers={'Content-Disposition':'attachment; filename="master-index-mappings.csv"'})


@router.get('')
async def listing(db: DbSession, user: CurrentUser, q: str=Query(default='',max_length=160), kind: Literal['patient','device','']=''):
    stmt=select(MasterIndex).where(*scope(MasterIndex,user))
    if kind: stmt=stmt.where(MasterIndex.kind==kind)
    if q.strip():
        q=q.strip()
        matches=select(MasterAlias.master_id).where(*scope(MasterAlias,user),MasterAlias.external_id.contains(q,autoescape=True))
        stmt=stmt.where(or_(MasterIndex.code.contains(q,autoescape=True),MasterIndex.name.contains(q,autoescape=True),MasterIndex.id.in_(matches)))
    items=list(await db.scalars(stmt.order_by(MasterIndex.created_at.desc(),MasterIndex.id).limit(501)))
    return {'items':[await info(i,db) for i in items[:500]],'truncated':len(items)>500}


async def save_aliases(item,data,db,user):
    await db.execute(delete(MasterAlias).where(MasterAlias.master_id==item.id))
    for alias in data.aliases:
        db.add(MasterAlias(id=str(uuid.uuid4()),master_id=item.id,kind=item.kind,**identity(user),**alias.model_dump()))
    try: await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409,'该业务系统编号已关联其他主索引，请核对后重试')
    await db.refresh(item)
    return await info(item,db)


@router.post('',status_code=201)
async def create(data: IndexInput, db: DbSession, user: CurrentUser):
    scope(MasterIndex,user)
    item=MasterIndex(id=str(uuid.uuid4()),name=data.name,kind=data.kind,code=('PAT-' if data.kind=='patient' else 'DEV-')+uuid.uuid4().hex.upper(),revision=1,**identity(user))
    db.add(item);await db.flush()
    return await save_aliases(item,data,db,user)


@router.get('/{item_id}')
async def read(item_id: str, db: DbSession, user: CurrentUser):
    return await info(await owned(item_id,db,user),db)


@router.put('/{item_id}')
async def edit(item_id: str, data: IndexInput, db: DbSession, user: CurrentUser):
    item=await owned(item_id,db,user)
    if item.kind!=data.kind: raise HTTPException(400,'主索引类型创建后不能修改')
    changed=await db.execute(update(MasterIndex).where(MasterIndex.id==item_id,*scope(MasterIndex,user),MasterIndex.revision==data.expected_revision).values(name=data.name,revision=MasterIndex.revision+1))
    if changed.rowcount!=1: raise HTTPException(409,'主索引已被修改，请刷新后重试')
    return await save_aliases(item,data,db,user)


@router.delete('/{item_id}')
async def remove(item_id: str, data: RevisionInput, db: DbSession, user: CurrentUser):
    await owned(item_id,db,user)
    # Lock/check the parent before removing children; any failure rolls back the transaction.
    changed=await db.execute(update(MasterIndex).where(MasterIndex.id==item_id,*scope(MasterIndex,user),MasterIndex.revision==data.expected_revision).values(revision=MasterIndex.revision+1))
    if changed.rowcount!=1: raise HTTPException(409,'主索引已被修改，请刷新后重试')
    await db.execute(delete(MasterAlias).where(MasterAlias.master_id==item_id))
    await db.execute(delete(MasterIndex).where(MasterIndex.id==item_id,*scope(MasterIndex,user)))
    await db.commit()
    return {'id':item_id}
