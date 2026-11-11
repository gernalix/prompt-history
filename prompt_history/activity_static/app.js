'use strict';
const $ = id => document.getElementById(id);
const names = {chatgpt:'ChatGPT · origine N/D',desktop:'Codex Desktop',cli:'Codex CLI',unknown:'Codex · origine N/D'};
const number = n => new Intl.NumberFormat('it-IT',{maximumFractionDigits:1}).format(n);
const compact = n => new Intl.NumberFormat('it-IT',{notation:'compact',maximumFractionDigits:1}).format(n);
const ns='http://www.w3.org/2000/svg';
function element(tag, attrs={}, text){const node=document.createElementNS(ns,tag);for(const [k,v] of Object.entries(attrs))node.setAttribute(k,v);if(text!==undefined)node.textContent=text;return node;}
function draw(data){
 const svg=$('chart');svg.replaceChildren();const rows=data.days;const left=65,top=20,w=915,h=255,bottom=top+h;const maximum=Math.max(1,...rows.flatMap(r=>[r.total,r.average7]));const max=maximum*1.12;
 for(let i=0;i<=4;i++){const y=bottom-h*i/4;svg.append(element('line',{x1:left,y1:y,x2:left+w,y2:y,class:'grid'}),element('text',{x:left-10,y:y+4,'text-anchor':'end'},compact(max*i/4)));}
 const step=w/Math.max(1,rows.length);let points=[];
 rows.forEach((r,i)=>{let y=bottom;for(const p of Object.keys(names)){const height=r[p]/max*h;const bar=element('rect',{x:left+i*step+step*.1,y:y-height,width:Math.max(.5,step*.8),height,class:p});bar.append(element('title',{},`${r.day} · ${names[p]}: ${number(r[p])}`));svg.append(bar);y-=height;}
 points.push(`${left+(i+.5)*step},${bottom-r.average7/max*h}`);
 if(i===0||i===rows.length-1||i%Math.max(1,Math.ceil(rows.length/6))===0)svg.append(element('text',{x:left+(i+.5)*step,y:bottom+27,'text-anchor':'middle'},r.day.slice(5).split('-').reverse().join('/')));
 });svg.append(element('polyline',{points:points.join(' '),class:'average'}));
 $('values').replaceChildren();for(const r of rows.slice().reverse()){const tr=document.createElement('tr');for(const value of [r.day,...Object.keys(names).map(p=>number(r[p])),number(r.total),number(r.average7)]){const td=document.createElement('td');td.textContent=value;tr.append(td);}$('values').append(tr);}
 $('empty').hidden=rows.some(r=>r.total>0);
 $('total').textContent=compact(data.total);$('total').title=number(data.total);$('active').textContent=`${data.active_days} / ${rows.length}`;$('mean').textContent=compact(rows.reduce((n,r)=>n+r.total,0)/Math.max(1,rows.length));
 const label={characters:'Caratteri',messages:'Messaggi',conversations:'Conversazioni'}[data.metric];$('total-label').textContent=label+' nel periodo';
 $('freshness').textContent=`Ultima elaborazione: ${new Date(data.generated_at).toLocaleString('it-IT',{timeZone:'Europe/Copenhagen'})}. Archivio aggiornato: ${data.source_updated_at?new Date(data.source_updated_at).toLocaleString('it-IT',{timeZone:'Europe/Copenhagen'}):'non disponibile'}.`;
 const q=data.quality; $('quality').textContent=`Copie Codex escluse: ${number(q.mirrors_excluded||0)}. Messaggi ancora privi di verifica della visibilità: ${number(q.visibility_pending||0)}. Timestamp mancanti: ${number(q.missing_timestamp||0)}.`;
 $('status').textContent=data.refresh_error?'Aggiornamento non riuscito: sono mostrati gli ultimi dati disponibili.':'Dati locali · aggiornamento automatico';
}
let request=0;
async function load(){const id=++request;const params=new URLSearchParams();for(const key of ['period','author','metric'])params.set(key,$(key).value);try{const response=await fetch('api/activity?'+params);const data=await response.json();if(id!==request)return;if(response.status===202){$('status').textContent='Preparazione dello storico…';setTimeout(load,3000);return;}if(!response.ok)throw Error();draw(data);}catch{if(id===request)$('status').textContent='Dati non disponibili. Nuovo tentativo automatico tra un minuto.';}}
for(const [p,label] of Object.entries(names)){const key=document.createElement('span');key.className='key';const dot=document.createElement('i');dot.className='swatch '+p;key.append(dot,document.createTextNode(label));$('legend').append(key);}
const mean=document.createElement('span');mean.className='key';mean.textContent='— Media mobile 7 giorni';$('legend').append(mean);
for(const key of ['period','author','metric'])$(key).addEventListener('change',load);
load();setInterval(load,60000);
