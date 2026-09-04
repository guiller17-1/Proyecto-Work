let allRecords = [];
let chart;

const els = {
  sede: document.getElementById('sedeFilter'), status: document.getElementById('statusFilter'),
  consider: document.getElementById('considerFilter'), turno: document.getElementById('turnoFilter'),
  limit: document.getElementById('limitFilter'), reset: document.getElementById('resetBtn'),
  total: document.getElementById('totalKpi'), avg: document.getElementById('avgKpi'),
  compliance: document.getElementById('complianceKpi'), duplicate: document.getElementById('duplicateKpi'),
  updated: document.getElementById('updatedAt'), body: document.getElementById('detailBody'),
  empty: document.getElementById('emptyState'), error: document.getElementById('errorBox')
};

const avg = values => {
  const valid = values.filter(v => Number.isFinite(v));
  return valid.length ? valid.reduce((a,b) => a+b, 0) / valid.length : 0;
};
const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c]));

function fillSelect(select, values) {
  [...new Set(values.filter(Boolean))].sort((a,b) => a.localeCompare(b, 'es')).forEach(value => {
    const option = document.createElement('option'); option.value = value; option.textContent = value; select.appendChild(option);
  });
}

function filteredRecords() {
  return allRecords.filter(r =>
    (!els.sede.value || r.sede === els.sede.value) &&
    (!els.status.value || r.status === els.status.value) &&
    (!els.consider.value || r.considerar === els.consider.value) &&
    (!els.turno.value || r.turno === els.turno.value)
  );
}

function render() {
  const rows = filteredRecords();
  els.total.textContent = rows.length.toLocaleString('es-PE');
  els.avg.textContent = `${avg(rows.map(r => r.tiempo)).toFixed(1)} min`;
  els.compliance.textContent = `${(avg(rows.filter(r => r.target != null).map(r => r.cumplimiento)) * 100).toFixed(1)}%`;
  els.duplicate.textContent = rows.filter(r => r.unicidad === 'Duplicado').length.toLocaleString('es-PE');

  const groups = new Map();
  rows.forEach(r => {
    if (!r.license) return;
    if (!groups.has(r.license)) groups.set(r.license, {tiempos:[], targets:[]});
    if (Number.isFinite(r.tiempo)) groups.get(r.license).tiempos.push(r.tiempo);
    if (Number.isFinite(r.target)) groups.get(r.license).targets.push(r.target);
  });
  const limit = Number(els.limit.value);
  const grouped = [...groups.entries()].map(([license, g]) => ({license, tiempo:avg(g.tiempos), target:avg(g.targets)}))
    .sort((a,b) => b.tiempo-a.tiempo).slice(0, limit);

  els.empty.hidden = grouped.length > 0;
  if (chart) chart.destroy();
  chart = new Chart(document.getElementById('mainChart'), {
    data: { labels: grouped.map(x => x.license), datasets: [
      {type:'bar', label:'Promedio TIEMPO', data:grouped.map(x=>x.tiempo), backgroundColor:'rgba(20,115,230,.78)', borderRadius:5, yAxisID:'y'},
      {type:'line', label:'Promedio TARGET', data:grouped.map(x=>x.target || null), borderColor:'#ef476f', backgroundColor:'#ef476f', borderWidth:3, pointRadius:3, tension:.25, yAxisID:'y'}
    ]},
    options: {responsive:true, maintainAspectRatio:false, interaction:{mode:'index',intersect:false},
      plugins:{legend:{position:'top'},tooltip:{callbacks:{label:ctx=>`${ctx.dataset.label}: ${Number(ctx.raw).toFixed(1)} min`}}},
      scales:{x:{ticks:{maxRotation:60,minRotation:35},grid:{display:false}},y:{beginAtZero:true,title:{display:true,text:'Minutos'}}}
    }
  });

  els.body.innerHTML = rows.slice().sort((a,b)=>(b.tiempo||0)-(a.tiempo||0)).slice(0,200).map(r => `<tr>
    <td>${escapeHtml(r.license)}</td><td>${escapeHtml(r.sede)}</td><td>${escapeHtml(r.status)}</td>
    <td>${escapeHtml(r.transaccion)}</td><td>${escapeHtml(r.containerType)}</td>
    <td>${r.tiempo == null ? '' : r.tiempo.toFixed(1)}</td><td>${r.target == null ? '' : r.target.toFixed(0)}</td>
    <td><span class="pill ${r.cumplimiento ? 'yes':'no'}">${r.cumplimiento ? 'Sí':'No'}</span></td></tr>`).join('');
}

async function loadData() {
  try {
    const response = await fetch('/api/data', {cache:'no-store'});
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'No se pudo cargar la información');
    allRecords = payload.records;
    fillSelect(els.sede, allRecords.map(r=>r.sede));
    fillSelect(els.status, allRecords.map(r=>r.status));
    els.updated.textContent = `Actualizado: ${new Date(payload.generatedAt).toLocaleString('es-PE')}`;
    render();
  } catch (error) {
    els.error.hidden = false; els.error.textContent = `Error: ${error.message}`; els.updated.textContent = 'Sin conexión con la fuente';
  }
}

[els.sede, els.status, els.consider, els.turno, els.limit].forEach(el => el.addEventListener('change', render));
els.reset.addEventListener('click', () => {els.sede.value='';els.status.value='';els.consider.value='CONSIDERAR';els.turno.value='';els.limit.value='15';render();});
loadData();
setInterval(() => location.reload(), 10 * 60 * 1000);
