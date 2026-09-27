const jsonata = require('jsonata');
const readline = require('node:readline');
let config;

function object(value, label) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error(`${label}必须是JSON对象`);
  return value;
}
function compile(semantic) {
  if (semantic.mode === 'script') return semantic.script;
  const seen = new Set();
  const entries = semantic.mappings.map(rule => {
    const source = semantic.sourceModel.fields.find(f => f.key === rule.source);
    const target = semantic.targetModel.fields.find(f => f.key === rule.target);
    if (!source || !target || seen.has(rule.target)) throw new Error('字段映射失效或重复');
    seen.add(rule.target);
    let expression = '$value';
    if (rule.kind === 'scale') {
      if (source.type !== 'number' || target.type !== 'number') throw new Error('单位换算需要数值字段');
      expression = `($value * ${rule.scale} + ${rule.offset})`;
    } else if (rule.kind === 'enum') {
      expression = `$lookup(${JSON.stringify(object(JSON.parse(rule.enumText),'枚举表'))}, $string($value))`;
    } else if (source.unit && target.unit && source.unit !== target.unit) throw new Error('单位不一致，需要配置换算');
    const fallback = rule.fallback.trim() ? JSON.stringify(JSON.parse(rule.fallback)) : 'null';
    return `${JSON.stringify(rule.target)}: ($value := $lookup($$, ${JSON.stringify(rule.source)}); $converted := $exists($value) and $value != null ? ${expression} : null; $exists($converted) and $converted != null ? $converted : ${fallback})`;
  });
  for (const f of semantic.targetModel.fields) if (!seen.has(f.key)) entries.push(`${JSON.stringify(f.key)}: null`);
  return `{${entries.join(',')}}`;
}
async function evaluate(script, input) {
  if (!script.trim() || script.length > 30000) throw new Error('脚本长度必须为1～30000字符');
  const value = object(await jsonata(script, {timeout:1500,stack:100,sequence:10000}).evaluate(input),'转换结果');
  const text = JSON.stringify(value, (_,v) => {
    if (typeof v === 'function' || (typeof v === 'number' && !Number.isFinite(v))) throw new Error('结果包含无效值');
    return v;
  });
  if (text.length > 200000) throw new Error('转换结果超过200KB');
  return JSON.parse(text);
}
function type(value) { return Array.isArray(value) ? 'array' : typeof value; }
async function transform(raw) {
  if (typeof raw !== 'string' || raw.length > 400000) throw new Error('报文必须是400KB以内的字符串');
  if (config.inputFormat === 'hex-json') {
    const compact = raw.replace(/\s/g,'');
    if (!compact || compact.length % 2 || !/^[0-9a-f]+$/i.test(compact)) throw new Error('十六进制报文格式无效');
    raw = new TextDecoder('utf-8',{fatal:true}).decode(Buffer.from(compact,'hex'));
  }
  const payload = object(JSON.parse(raw),'设备报文');
  if (payload.productKey != null && payload.productKey !== config.productKey) throw new Error('报文产品标识与插件不一致');
  const parsed = await evaluate(config.syntax.script, {payload,params:object(JSON.parse(config.parametersText),'协议参数')});
  for (const f of config.semantic.sourceModel.fields) if (parsed[f.key] != null && type(parsed[f.key]) !== f.type) throw new Error(`源字段 ${f.name} 类型不符`);
  const output = {...Object.fromEntries(config.semantic.targetModel.fields.map(f=>[f.key,null])),...await evaluate(compile(config.semantic),parsed)};
  if (Object.keys(output).some(k=>!config.semantic.targetModel.fields.some(f=>f.key===k))) throw new Error('输出包含目标模型未定义字段');
  for (const f of config.semantic.targetModel.fields) {
    const v=output[f.key];
    if (v==null) continue;
    if (type(v)!==f.type || (f.type==='number' && (!Number.isFinite(v) || f.minimum!=null && v<f.minimum || f.maximum!=null && v>f.maximum))) throw new Error(`目标字段 ${f.name} 类型或范围不符`);
  }
  return {output,device_id:String(payload.deviceId??''),empty_fields:Object.keys(output).filter(k=>output[k]==null)};
}
async function main() {
  for await (const line of readline.createInterface({input:process.stdin,crlfDelay:Infinity})) {
    try {
      const request=JSON.parse(line);const started=Date.now();
      if (request.command==='initialize') {
        config=request.settings;
        const result=await transform(config.raw);
        process.stdout.write(JSON.stringify({ready:true,...result,duration_ms:Date.now()-started})+'\n');
      } else {
        if (!config) throw new Error('插件尚未初始化');
        process.stdout.write(JSON.stringify({...await transform(request.raw),duration_ms:Date.now()-started})+'\n');
      }
    } catch (error) { process.stdout.write(JSON.stringify({error:String(error.message||error).slice(0,1000)})+'\n'); }
  }
}
main().catch(()=>process.exit(1));
