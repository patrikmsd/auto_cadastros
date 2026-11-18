const $ = id => document.getElementById(id);
const PADRAO = "http://127.0.0.1:8780";

function mostrar(texto, ok) {
  $("estado").textContent = texto;
  $("estado").className = ok ? "ok" : "erro";
  $("ponto").className = ok ? "ok" : "erro";
}

async function testar() {
  const r = await chrome.runtime.sendMessage({tipo: "quem"});
  if (r?.status === 200) {
    const d = r.dados, rod = d.rodando_agora;
    mostrar(`Conectado como ${d.analista}. Fila: ${d.na_fila} esperando${rod ? ` · analisando ${rod.placa || rod.pedido}` : ""}.`, true);
  } else {
    mostrar(r?.dados?.erro || r?.erro || `erro ${r?.status}`, false);
  }
}

async function salvar() {
  let servidor = $("servidor").value.trim() || PADRAO;
  if (!/^https?:\/\//.test(servidor)) servidor = "http://" + servidor;
  if (!/:\d+\/?$/.test(servidor)) servidor = servidor.replace(/\/?$/, ":8780");
  $("servidor").value = servidor;
  await chrome.storage.local.set({servidor, chave: $("chave").value.trim()});
  $("ponto").className = "";
  $("estado").className = "ok";
  $("estado").textContent = "testando…";
  testar();
}

chrome.storage.local.get({servidor: PADRAO, chave: ""}).then(c => {
  $("servidor").value = c.servidor;
  $("chave").value = c.chave;
  testar();
});

$("salvar").addEventListener("click", salvar);
for (const id of ["servidor", "chave"]) $(id).addEventListener("keydown", e => e.key === "Enter" && salvar());
