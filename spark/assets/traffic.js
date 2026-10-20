const el=id=>document.getElementById(id);
const gb=n=>(n/1e9).toLocaleString('zh-CN',{maximumFractionDigits:3});
async function update(){
  try{
    const response=await fetch('/api/traffic',{cache:'no-store'});
    if(!response.ok)throw new Error('not ready');
    const d=await response.json();
    el('month').textContent=d.month+' · UTC 自然月';
    for(const key of ['upload','download','total'])el(key).textContent=gb(d[key])+(key==='total'?'':' GB');
    el('progress').value=Math.min(100,d.total/d.quota_bytes*100);
    el('progress').parentElement.setAttribute('aria-valuenow',el('progress').value);
    el('quota').textContent=`前 ${gb(d.quota_bytes)} GB 为高速流量参考额度 · ${d.over_reference?'已超过参考额度，仅提醒':'已用 '+(d.total/d.quota_bytes*100).toFixed(1)+'%'}`;
    el('quota').className=d.over_reference?'warn':'';
    el('status').textContent=d.stale?'统计更新延迟':d.service_active===true?'服务运行中':d.service_active===false?'服务未运行':'服务状态未知';
    el('status').className=d.stale||d.service_active!==true?'warn':'';
    el('reset').textContent=new Date(d.next_reset_at*1000).toISOString().slice(0,10);
    el('updated').textContent='更新于 '+new Date(d.updated_at*1000).toLocaleString()+(d.boundary_estimated?' · 跨月采样间隔按时间估算分摊':'');
    const rows=Object.entries(d.protocols).sort((a,b)=>(b[1].upload+b[1].download)-(a[1].upload+a[1].download));
    el('protocols').replaceChildren(...rows.map(([protocol,v])=>{
      const row=document.createElement('div');row.className='row';
      const label=document.createElement('span');label.textContent=protocol;
      const bar=document.createElement('progress');bar.max=100;bar.value=d.total?(v.upload+v.download)/d.total*100:0;
      bar.setAttribute('aria-label',protocol+' 占比');
      const value=document.createElement('span');value.textContent=gb(v.upload+v.download)+' GB';
      row.append(label,bar,value);return row;
    }));
  }catch{el('status').textContent='数据暂不可用';el('status').className='warn';}
}
update();setInterval(update,30000);
