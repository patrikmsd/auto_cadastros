// Assistente de conferência dentro do PowerCRM: quando um card está aberto, mostra o que o robô encontrou.
// Nunca clica em Salvar, Atender, Aprovar ou Reprovar: "preencher" só digita o valor na tela; quem salva é o analista.
(() => {
  const TEXTO = {ok: "confere", sugerido: "sugestão da IA", conferir: "conferir", diverge: "diverge", preenchido: "preenchido pela IA · falta salvar"};
  const SIT = {OK: ["ok", "OK"], VERIFICAR: ["conferir", "Verificar"], DIVERGENTE: ["diverge", "Divergente"]};
  const STATUS_ITEM = {OK: "ok", ALERTA: "conferir", NAO_VERIFICAVEL: "conferir", DIVERGENTE: "diverge"};

  let codigo = null, res = null, servidor = null, minimizado = false, raiz = null, popoverDe = null;
  let preenchidos = {};  // nome do campo → {valor posto, valor que estava antes}
  let errados = {};      // nome do campo → o analista marcou a sugestão/conferência como errada
  let rascunho = "";     // texto da observação ainda não enviado (sobrevive aos redesenhos do painel)
  let retornoAberto = false, enviando = false;  // caixa "o que está errado" no rodapé do painel
  const esc = s => String(s ?? "").replace(/[&<>"]/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"}[c]));
  const so = s => String(s ?? "").normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/\W/g, "").toUpperCase();
  const pedir = msg => new Promise(ok => chrome.runtime.sendMessage(msg, r => ok(r || {status: 0})));
  const visivel = el => el && el.getClientRects().length > 0 && getComputedStyle(el).visibility !== "hidden";
  // preenchido agora > o que a tela mostra ao vivo (sugestão que já está no campo vira "confere") > o que o servidor disse
  const estadoDe = c => c.nome in preenchidos ? "preenchido" : (c._vivo || c.status);

  // o card aberto é o modal com "ID: <código>" no topo (o aviso de duplicata também é modal, mas não tem ID)
  function cardAberto() {
    for (const m of document.querySelectorAll("div.modal")) {
      if (!visivel(m)) continue;
      const id = m.textContent.match(/ID:\s*([A-Za-z0-9]{6,})/);
      if (id) return {modal: m, codigo: id[1]};
    }
    return null;
  }

  function acharCampo(modal, c) {
    let el = modal.querySelector(`#${CSS.escape(c.nome)}, [name="${CSS.escape(c.nome)}"]`);
    if (!el) {  // sem id na tela: pelo rótulo
      const rotulo = [...modal.querySelectorAll("label, .control-label, span, strong")]
        .find(l => l.children.length === 0 && l.textContent.trim() === c.rotulo && visivel(l));
      const caixa = rotulo && (rotulo.closest(".form-group, [class*='col-']") || rotulo.parentElement);
      el = caixa && caixa.querySelector("select, input:not([type=hidden]):not([type=checkbox]), button");
    }
    return el && visivel(visualDe(el)) ? el : null;
  }

  // select escondido por um componente (chosen nas listas do veículo, fSelect na Categoria): na tela aparece o componente
  function visualDe(el) {
    if (visivel(el)) return el;
    return (el.id && document.getElementById(`${el.id}_chosen`)) || el.closest(".fs-wrap") || el.parentElement.querySelector(":scope > .fs-wrap") ||
      [...el.parentElement.children].find(x => x !== el && !x.classList.contains("asx-selo") && visivel(x)) || el;
  }

  function campoNaTela(c) {
    const a = cardAberto();
    return a ? acharCampo(a.modal, c) : null;
  }

  const valorDe = el => el.tagName === "SELECT" ? [...el.selectedOptions].map(o => o.text).join(", ")
    : el.tagName === "BUTTON" ? el.textContent.trim() : el.value;
  const vazio = el => /^(selecione.*|não informado|nenhum.*|)$/i.test(valorDe(el).trim());
  const letras = t => so(t).split("").sort().join("");
  const bate = (el, c) => el.multiple ? letras(valorDe(el)) === letras(c.encontrado) : so(valorDe(el)) === so(c.encontrado);

  // ---------------- ciclo: acompanha qual card está aberto ----------------
  function ciclo() {
    const a = cardAberto();
    if (!a) {
      if (codigo) { codigo = null; res = null; servidor = null; central = null; limparTela(); desenhar(); desenharCentral(); }
      return;
    }
    if (a.codigo !== codigo) {
      limparTela();
      codigo = a.codigo; res = null; servidor = {estado: "carregando"}; preenchidos = {}; errados = {}; rascunho = ""; retornoAberto = false;
      imagens.clear(); vivo = null; grandes.clear(); minis.clear(); central = null;
      desenhar();
      desenharCentral();
      carregar();
    }
    if (res) marcarCampos(a.modal);
  }

  function limparTela() {
    document.querySelectorAll(".asx-fantasma, .asx-moldura, .asx-selo").forEach(f => f.remove());
    document.querySelectorAll(".asx-campo-diverge, .asx-campo-conferir").forEach(e => e.classList.remove("asx-campo-diverge", "asx-campo-conferir"));
    fecharPopover();
  }

  async function carregar() {
    const c = codigo;
    const r = await pedir({tipo: "resultado", chave: c});
    if (c !== codigo) return;
    if (r.status === 200) { res = r.dados; servidor = null; vivo = null; imagens.clear(); }
    else {
      res = null;
      servidor = r.status === 0 ? {erro: r.erro || "servidor do assistente desligado"} : (r.dados || {});
      if (["fila", "rodando"].includes(servidor.estado)) setTimeout(() => c === codigo && carregar(), 2500);
    }
    desenhar();
  }

  // ---------------- selos, valor fantasma e brilho nos campos ----------------
  function marcarCampos(modal) {
    for (const c of res.campos) {
      const el = acharCampo(modal, c);
      if (!el) continue;
      const v = visualDe(el);
      // sugestão que alguém já pôs no campo: confere (verde); outro valor no campo: diverge
      if (c.status === "sugerido" && !(c.nome in preenchidos)) c._vivo = vazio(el) ? null : bate(el, c) ? "ok" : "diverge";
      const estado = estadoDe(c);
      const caixa = v.closest(".form-group, [class*='col-']") || v.parentElement;
      let selo = caixa.querySelector(`:scope > .asx-selo[data-campo="${c.nome}"]`);
      if (!selo) {
        selo = document.createElement("span");
        selo.dataset.campo = c.nome;
        selo.addEventListener("click", ev => {
          ev.preventDefault(); ev.stopPropagation();
          const atual = res?.campos.find(x => x.nome === selo.dataset.campo);
          if (atual) abrirPopover(atual, selo);
        });
        caixa.appendChild(selo);
      }
      const texto = (estado === "conferir" && /^não encontrado/.test(c.encontrado) ? "não encontrado" : TEXTO[estado]) +
                    (errados[c.nome] ? " · marcado errado" : "");
      if (selo.dataset.estado !== estado || selo.textContent !== texto) {
        selo.className = `asx-selo asx-${estado}`;
        selo.dataset.estado = estado;
        selo.textContent = texto;
        selo.title = `${c.rotulo}: clique para ver a fonte`;
      }
      v.classList.remove("asx-campo-diverge", "asx-campo-conferir");
      if (estado === "diverge" || estado === "conferir") v.classList.add(`asx-campo-${estado}`);
      // moldura: girando enquanto há sugestão, roxa fixa depois de preenchida, verde fixa onde já bate
      sobrepor(v, c, "asx-moldura", {sugerido: "asx-anima", preenchido: "asx-feito", ok: "asx-certo"}[estado] || "", -3);
      sobrepor(v, c, "asx-fantasma", estado === "sugerido" && vazio(el) ? "asx-mostra" : "", 1, c.encontrado);
    }
  }

  // camada por cima do campo (não mexe no valor real): moldura e valor sugerido "fantasma"
  function sobrepor(el, c, classe, modo, folga, texto = "") {
    const pai = el.parentElement;
    let f = pai.querySelector(`:scope > .${classe}[data-campo="${c.nome}"]`);
    if (!modo) { f?.remove(); return; }
    if (getComputedStyle(pai).position === "static") pai.style.position = "relative";
    if (!f) {
      f = document.createElement("span");
      f.dataset.campo = c.nome;
      pai.appendChild(f);
    }
    f.className = `${classe} ${modo}`;
    const seta = classe === "asx-fantasma" && el.tagName !== "INPUT" ? 26 : 0;
    Object.assign(f.style, {left: `${el.offsetLeft + folga}px`, top: `${el.offsetTop + folga}px`,
                            width: `${el.offsetWidth - 2 * folga - seta}px`, height: `${el.offsetHeight - 2 * folga}px`});
    if (texto && f.dataset.valor !== texto) { f.dataset.valor = texto; f.textContent = texto; }
  }

  // ---------------- preencher (só a tela; o analista salva) ----------------
  function preencher(c, silencioso = false) {
    const el = campoNaTela(c);
    if (!el) { if (!silencioso) aviso(`Abra a aba ${c.aba} do card para preencher ${c.rotulo}.`); return false; }
    const antes = valorDe(el), valor = c.encontrado;
    if (el.tagName === "SELECT" && el.multiple) {
      marcarMultipla(el, valor);
    } else if (el.tagName === "SELECT") {
      const opcoes = [...el.options];
      const op = opcoes.find(o => so(o.text) === so(valor)) || opcoes.find(o => so(valor) && so(o.text).includes(so(valor)));
      if (!op) { if (!silencioso) aviso(`A lista de ${c.rotulo} não tem a opção "${valor}". Escolha à mão.`); return false; }
      el.value = op.value;
      avisarMudanca(el);
    } else {
      el.focus();
      Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set.call(el, valor);
      el.dispatchEvent(new KeyboardEvent("keyup", {bubbles: true}));
      avisarMudanca(el);
    }
    preenchidos[c.nome] = {valor: valorDe(el), antes};
    if (!silencioso) {
      fecharPopover();
      aviso(bate(el, c) ? `${c.rotulo} preenchido. Confira e clique em Salvar.`
        : `${c.rotulo}: a tela ficou "${valorDe(el)}" (esperado "${valor}"). Confira antes de salvar.`, bate(el, c));
      atualizarTudo();
    }
    return true;
  }

  // o CRM escuta change; as listas com "chosen" só redesenham com chosen:updated
  function avisarMudanca(el) {
    for (const tipo of ["input", "change", "chosen:updated"]) el.dispatchEvent(new Event(tipo, {bubbles: true}));
    el.dispatchEvent(new Event("blur"));
  }

  // Categoria da CNH: "AB" → marca A e B. No CRM é um fSelect: clica nas opções dele (ele atualiza o select e o rótulo)
  function marcarMultipla(el, valor) {
    const quer = new Set(so(valor).split(""));
    const wrap = el.closest(".fs-wrap") || el.parentElement.querySelector(":scope > .fs-wrap");
    if (wrap) {
      for (const op of wrap.querySelectorAll(".fs-options .fs-option")) {
        const letra = so(op.querySelector(".fs-option-label")?.textContent);
        if (quer.has(letra) !== op.classList.contains("selected")) op.click();
      }
    }
    if (letras(valorDe(el)) !== letras(valor)) {  // sem fSelect (ou ele não respondeu): marca no próprio select
      for (const o of el.options) o.selected = quer.has(so(o.text));
      avisarMudanca(el);
    }
  }

  function preencherTudo() {
    const fila = res.campos.filter(c => estadoDe(c) === "sugerido" && c.pode_preencher && !errados[c.nome]);
    const feitos = fila.filter(c => preencher(c, true));
    const outras = [...new Set(fila.filter(c => !(c.nome in preenchidos)).map(c => c.aba))];
    aviso(`${feitos.length} campo(s) preenchido(s). Confira e clique em Salvar.` +
          (outras.length ? ` Abra a aba ${outras.join(" / ")} e clique em Preencher tudo de novo para o resto.` : ""), feitos.length > 0);
    atualizarTudo();
  }

  function desfazer(c) {
    const el = campoNaTela(c), p = preenchidos[c.nome];
    if (!el || !p) return aviso(`Abra a aba ${c.aba} para desfazer ${c.rotulo}.`);
    if (el.multiple) marcarMultipla(el, so(p.antes));
    else if (el.tagName === "SELECT") { el.value = ([...el.options].find(o => o.text === p.antes) || el.options[0]).value; avisarMudanca(el); }
    else { Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), "value").set.call(el, p.antes); avisarMudanca(el); }
    delete preenchidos[c.nome];
    atualizarTudo();
  }

  function atualizarTudo() {
    desenhar();
    const a = cardAberto();
    if (a && res) marcarCampos(a.modal);
  }

  function irAoCampo(c) {
    const el = campoNaTela(c);
    if (!el) return aviso(`${c.rotulo} fica na aba ${c.aba}: abra essa aba do card.`);
    const v = visualDe(el);
    v.scrollIntoView({behavior: "smooth", block: "center"});
    v.classList.add("asx-destaque");
    setTimeout(() => v.classList.remove("asx-destaque"), 2200);
  }

  // ---------------- observação do analista ----------------
  // o nome do analista vem da chave dele (servidor); não precisa digitar
  async function enviarObservacao() {
    if (enviando) return;
    const texto = (raiz.getElementById("obs")?.value ?? rascunho).trim();
    const marcados = res.campos.filter(c => errados[c.nome]);
    if (!texto && !marcados.length) return aviso("Marque algum item como errado ou escreva o que estava errado.");
    enviando = true;
    desenhar();
    let r;
    try {
      r = await pedir({tipo: "observacao", dados: {
        codigo: res.codigo, placa: res.placa, texto,
        campos_errados: marcados.map(c => ({nome: c.nome, rotulo: c.rotulo, status: c.status, sugerido: c.encontrado, no_card: c.atual})),
        preenchidos: Object.fromEntries(Object.entries(preenchidos).map(([k, v]) => [k, v.valor])),
      }});
    } finally {
      enviando = false;
    }
    if (r.status !== 200) {  // o texto e as marcações continuam aqui para tentar de novo
      desenhar();
      return aviso(`Não foi enviado: ${r.dados?.erro || r.erro || `erro ${r.status}`}. O que você escreveu continua aqui.`);
    }
    res.observacoes = r.dados.observacoes;
    rascunho = "";
    errados = {};
    retornoAberto = false;
    aviso("Enviado. Obrigado: isso ajuda a corrigir o robô.", true);
    atualizarTudo();
  }

  // ---------------- painel lateral (shadow DOM: o CSS do CRM não interfere) ----------------
  function montarRaiz() {
    const host = document.createElement("div");
    host.id = "asx-assistente";
    host.style.cssText = "all:initial;position:fixed;z-index:2147483646;top:0;left:0;width:0;height:0";
    document.documentElement.appendChild(host);
    raiz = host.attachShadow({mode: "open"});
    raiz.innerHTML = `<style>${CSS_PAINEL}</style><div id="painel"></div><div id="central" hidden></div><div id="pop" hidden></div><div id="toast" hidden></div><div id="zoom" hidden></div>`;
    raiz.addEventListener("input", ev => { if (ev.target.id === "obs") rascunho = ev.target.value; });
    // O card do CRM é um modal Bootstrap que "puxa o foco" de volta quando algo fora dele é focado (enforceFocus):
    // sem isto não dá para digitar na observação. As teclas digitadas aqui também não vão para os atalhos do CRM.
    // O CRM usa jQuery 1.12 + Bootstrap 3.3.7: o "focusin" do jQuery é um listener de "focus" em CAPTURA no document,
    // que roda antes de qualquer coisa no painel. Por isso a captura é barrada já na window (que vem antes do document).
    for (const tipo of ["focus", "focusin"]) window.addEventListener(tipo, ev => {
      if (ev.target === host) ev.stopImmediatePropagation();
    }, true);
    for (const tipo of ["focusin", "focusout", "keydown", "keyup", "keypress"]) host.addEventListener(tipo, ev => {
      if (tipo.startsWith("key") && ev.key === "Escape" && !ev.composedPath().some(e => e.tagName === "TEXTAREA")) return;
      ev.stopPropagation();
    });
    // central de fotos: roda do mouse = zoom no ponto, arrastar = mover a foto ampliada, duplo clique = ampliar/ajustar
    const palco = ev => central && ev.target.closest?.("#palco") && !ev.target.closest("button") ? ev.target.closest("#palco") : null;
    const ponto = (p, ev) => { const r = p.getBoundingClientRect(); return [ev.clientX - r.left - r.width / 2, ev.clientY - r.top - r.height / 2]; };
    raiz.addEventListener("wheel", ev => { const p = palco(ev); if (!p) return; ev.preventDefault(); zoom(ev.deltaY < 0 ? 1.25 : 1 / 1.25, ...ponto(p, ev)); }, {passive: false});
    raiz.addEventListener("dblclick", ev => { const p = palco(ev); if (p) central.z > 1 ? ajustar() : zoom(2.5, ...ponto(p, ev)); });
    raiz.addEventListener("pointerdown", ev => {
      const p = palco(ev);
      if (!p || central.z <= 1) return;
      arrasto = {x: ev.clientX - central.x, y: ev.clientY - central.y};
      p.setPointerCapture(ev.pointerId);
      p.classList.add("arrastando");
    });
    raiz.addEventListener("pointermove", ev => { if (!arrasto) return; central.x = ev.clientX - arrasto.x; central.y = ev.clientY - arrasto.y; aplicarZoom(); });
    const solta = () => { arrasto = null; raiz.getElementById("palco")?.classList.remove("arrastando"); };
    raiz.addEventListener("pointerup", solta);
    raiz.addEventListener("pointercancel", solta);
    raiz.addEventListener("click", ev => {
      const b = ev.target.closest("[data-acao]");
      if (!b) return;
      const c = res?.campos.find(x => x.nome === b.dataset.nome);
      ({
        min: () => { minimizado = true; desenhar(); },
        max: () => { minimizado = false; desenhar(); },
        ir: () => irAoCampo(c),
        por: () => preencher(c),
        tudo: preencherTudo,
        desfazer: () => desfazer(c),
        errado: () => { errados[c.nome] = !errados[c.nome]; atualizarTudo(); if (popoverDe) abrirPopover(c, popoverDe, true); },
        observar: enviarObservacao,
        retorno: () => { retornoAberto = true; desenhar(); raiz.getElementById("obs")?.focus(); },
        "retorno-fechar": () => { retornoAberto = false; desenhar(); },
        copiar: () => navigator.clipboard.writeText(c.encontrado).then(() => aviso(`${c.rotulo} copiado.`, true)),
        foto: () => res?.fotos?.some(f => f.rotulo === b.dataset.rotulo) ? abrirCentral(b.dataset.rotulo) : verFoto(b.dataset.rotulo),
        central: () => abrirCentral(),
        "c-fechar": fecharCentral,
        "c-ant": () => irPara(-1),
        "c-prox": () => irPara(1),
        "c-ir": () => { central.i = Number(b.dataset.i); ajustar(false); central.giro = 0; desenharCentral(); },
        "c-filtro": () => { central.filtro = b.dataset.f; const l = visiveis(); if (l.length && !l.includes(res.fotos[central.i])) central.i = res.fotos.indexOf(l[0]); desenharCentral(); },
        "c-mais": () => zoom(1.4, 0, 0),
        "c-menos": () => zoom(1 / 1.4, 0, 0),
        "c-ajustar": () => ajustar(),
        "c-girar": () => { central.giro = (central.giro + 90) % 360; aplicarZoom(); },
        analisar: async () => {
          const r = await pedir({tipo: "analisar", chave: codigo});
          servidor = r.status === 200 ? {estado: "fila", ...r.dados} : {erro: r.dados?.erro || r.erro || `erro ${r.status}`, proibido: r.status === 403};
          desenhar();
          if (r.status === 200) setTimeout(carregar, 1500);
        },
        opcoes: () => pedir({tipo: "opcoes"}),
        recarregar: () => { servidor = {estado: "carregando"}; desenhar(); carregar(); },
        fecharpop: fecharPopover,
        fecharzoom: () => { raiz.getElementById("zoom").hidden = true; },
      }[b.dataset.acao] || (() => {}))();
    });
  }

  const pill = (c, t) => `<span class="pill ${c}"><i></i>${t}</span>`;

  function itemCampo(c) {
    const estado = estadoDe(c);
    const botoes = [
      `<button data-acao="ir" data-nome="${esc(c.nome)}">ir</button>`,
      c.foto ? `<button data-acao="foto" data-rotulo="${esc(c.foto)}">foto</button>` : "",
      estado === "preenchido" ? `<button data-acao="desfazer" data-nome="${esc(c.nome)}">desfazer</button>` : "",
      c.pode_preencher && c.encontrado && estado === "sugerido" ? `<button class="pri" data-acao="por" data-nome="${esc(c.nome)}">preencher</button>` : "",
    ].join("");
    const valor = estado === "sugerido" || estado === "preenchido" ? `<div class="valor">${esc(estado === "preenchido" ? preenchidos[c.nome].valor : c.encontrado)}</div>`
      : estado === "ok" ? "" : `<div class="de-para"><span>no card: <b>${esc(c.atual || "(vazio)")}</b></span>${c.encontrado ? `<span>encontrado: <b>${esc(c.encontrado)}</b></span>` : ""}</div>`;
    return `<div class="item ${estado}${errados[c.nome] ? " errado" : ""}"><div class="linha"><span class="rot"><i></i>${esc(c.rotulo)} <small>${esc(c.aba)}</small></span>
      <span class="botoes">${botoes}</span></div>${valor}<div class="fonte">${esc(c.fonte)}</div>
      ${c.nota ? `<details><summary>${esc(c.nota.slice(0, 90))}${c.nota.length > 90 ? "…" : ""}</summary>${esc(c.nota)}</details>` : ""}
      <label class="marca"><input type="checkbox" data-acao="errado" data-nome="${esc(c.nome)}" ${errados[c.nome] ? "checked" : ""}> está errado</label></div>`;
  }

  function desenhar() {
    if (!raiz) montarRaiz();
    const p = raiz.getElementById("painel");
    if (!codigo) { p.innerHTML = ""; fecharPopover(); return; }
    const faltam = res ? res.campos.filter(c => estadoDe(c) === "sugerido" && c.pode_preencher && !errados[c.nome]) : [];
    if (minimizado) {
      p.innerHTML = `<button class="bolha" data-acao="max">Assistente · ${esc(res?.placa || codigo)}${faltam.length ? ` · ${faltam.length} para preencher` : ""}</button>`;
      return;
    }
    let corpo;
    if (!res) {
      const s = servidor || {};
      corpo = s.erro && !s.estado && s.erro !== "card não analisado" ? `<div class="vazio">${esc(s.erro)}<br><button data-acao="recarregar">tentar de novo</button>
           <button data-acao="opcoes">opções da extensão</button></div>`
        : s.estado === "carregando" ? `<div class="vazio"><span class="giro"></span> buscando o resultado…</div>`
        : ["fila", "rodando"].includes(s.estado) ? analisando(s)
        : `<div class="vazio">Este card ainda não foi analisado${s.estado === "erro" ? " (a última tentativa falhou)" : ""}.<br>
           <button class="pri" data-acao="analisar">Analisar este card</button><br><small>O robô só lê o CRM: nada é gravado.</small></div>`;
    } else {
      const grupo = st => res.campos.filter(c => estadoDe(c) === st);
      const [sug, pre, div, conf, ok] = ["sugerido", "preenchido", "diverge", "conferir", "ok"].map(grupo);
      const gerais = res.conferencia.filter(i => i.status !== "OK" && !i.no_campo);  // o que não tem campo na tela (mensalidade, fotos...)
      const v = res.vistoria, f = res.fipe, obs = res.observacoes || [];
      corpo = `
        <div class="contagem">${pill("sugerido", `${sug.length} para preencher`)}${pill("preenchido", `${pre.length} preenchido(s)`)}${pill("diverge", `${div.length} diverge`)}${pill("conferir", `${conf.length} conferir`)}${pill("ok", `${ok.length} confere`)}</div>
        ${(res.avisos || []).map(t => `<div class="alerta">${esc(t)}</div>`).join("")}
        ${pre.length ? `<div class="salvar">${pre.length} campo(s) preenchido(s) pela IA na tela. <b>Confira e clique em Salvar.</b> O robô não salva.</div>` : ""}
        ${sug.length ? `<h3>Para preencher</h3>${sug.map(itemCampo).join("")}` : ""}
        ${pre.length ? `<h3>Preenchidos pela IA (falta salvar)</h3>${pre.map(itemCampo).join("")}` : ""}
        ${div.length ? `<h3>Divergências</h3>${div.map(itemCampo).join("")}` : ""}
        ${conf.length ? `<h3>Para conferir</h3>${conf.map(itemCampo).join("")}` : ""}
        ${gerais.length ? `<h3>Conferência geral</h3>${gerais.map(i => `<div class="item ${STATUS_ITEM[i.status]}"><div class="rot"><i></i>${esc(i.nome)}</div>
            <details><summary>${esc(i.detalhe.slice(0, 110))}${i.detalhe.length > 110 ? "…" : ""}</summary>${esc(i.detalhe)}</details></div>`).join("")}` : ""}
        ${f && f.valor ? `<h3>FIPE oficial · ${esc(f.mes)}</h3><div class="item ${STATUS_ITEM[f.status] || "conferir"}"><div class="rot"><i></i>${esc(f.modelo)}</div>
            <div class="valor">${esc(f.valor)} <small>código ${esc(f.codigo)}</small></div><div class="fonte">autenticação ${esc(f.autenticacao)}</div></div>` : ""}
        ${v || res.fotos?.length ? resumoFotos(v) : ""}
        ${res.incertezas.length ? `<details class="bloco"><summary>${res.incertezas.length} informação(ões) sem certeza</summary>${res.incertezas.map(x =>
            `<div class="item conferir"><div class="rot"><i></i>${esc(x.campo)}: ${esc(x.valor || "-")}</div><div class="fonte">${esc(x.motivo)} · ${esc(x.fonte)}</div></div>`).join("")}</details>` : ""}
        ${ok.length ? `<details class="bloco"><summary>${ok.length} campo(s) conferido(s) e batendo</summary>${ok.map(itemCampo).join("")}</details>` : ""}
        ${obs.length ? `<details class="bloco"><summary>${obs.length} observação(ões) já enviada(s) deste card</summary>${obs.map(o =>
            `<div class="anterior"><b>${esc(o.analista || "sem nome")}</b> · ${esc(o.data_hora)}<br>${esc(o.texto)}${o.campos_errados?.length ? `<br><small>errados: ${esc(o.campos_errados.map(x => x.rotulo).join(", "))}</small>` : ""}</div>`).join("")}</details>` : ""}
        <div class="rodape">Analisado em ${esc(res.analisado_em)}${res.segundos ? ` · ${res.segundos}s` : ""}${res.custo_rs != null ? ` · R$ ${String(res.custo_rs).replace(".", ",")} de IA` : ""}<br>
          Nada foi salvo pelo robô. Quem confere e salva é você. <button data-acao="analisar">analisar de novo</button></div>`;
    }
    const sit = res && SIT[res.situacao];
    // redesenhar não pode tirar o analista do lugar: guarda rolagem, foco/cursor e o que estava aberto
    const antes = p.querySelector(".corpo")?.scrollTop || 0, foco = raiz.activeElement;
    const focoId = foco?.id, sel = foco && "selectionStart" in foco ? [foco.selectionStart, foco.selectionEnd] : null;
    const abertos = new Set([...p.querySelectorAll("details[open] > summary")].map(s => s.textContent));
    p.innerHTML = `<div class="painel"><div class="topo"><div class="titulo"><b>Assistente</b> <span class="mono">${esc(res?.placa || "")}</span>${sit ? pill(...sit) : ""}
        <div class="sub">${esc(res?.veiculo || codigo)}</div></div><button class="x" data-acao="min" title="minimizar">–</button></div>
        ${faltam.length ? `<button class="tudo" data-acao="tudo">Preencher tudo <span>${faltam.length}</span></button>` : ""}
        <div class="corpo">${corpo}</div>${res ? caixaRetorno() : ""}</div>`;
    for (const s of p.querySelectorAll("details > summary")) if (abertos.has(s.textContent)) s.parentElement.open = true;
    const corpoNovo = p.querySelector(".corpo");
    if (corpoNovo) corpoNovo.scrollTop = antes;
    const novoFoco = focoId && raiz.getElementById(focoId);
    if (novoFoco) { novoFoco.focus({preventScroll: true}); if (sel) try { novoFoco.setSelectionRange(...sel); } catch {} }
  }

  // Caixa de retorno fixa no rodapé do painel (não precisa rolar): o que foi marcado "está errado" entra na hora como etiqueta
  function caixaRetorno() {
    const marcados = res.campos.filter(c => errados[c.nome]);
    const aberta = retornoAberto || marcados.length || rascunho;
    if (!aberta) return `<button class="retorno-abrir" data-acao="retorno">✎ Algo errado neste card? Conte para melhorar o robô</button>`;
    const etiqueta = c => `<span class="etq" title="${esc(`IA: ${c.encontrado || "-"} · no card: ${c.atual || "(vazio)"}`)}">
        <b>${esc(c.rotulo)}</b> <small>IA: ${esc(c.encontrado || "-")}</small><button data-acao="errado" data-nome="${esc(c.nome)}" title="tirar">×</button></span>`;
    return `<div class="retorno">
      <div class="linha"><b>O que está errado?</b>${marcados.length || rascunho ? "" : `<button class="x" data-acao="retorno-fechar" title="fechar">–</button>`}</div>
      ${marcados.length ? `<div class="etqs">${marcados.map(etiqueta).join("")}</div>`
        : `<div class="fonte">Marque <b>está errado</b> nos itens acima e eles entram aqui, ou só escreva.</div>`}
      <textarea id="obs" rows="2" placeholder="Explique o que estava errado (opcional se marcou os itens)">${esc(rascunho)}</textarea>
      <button class="pri enviar" data-acao="observar"${enviando ? " disabled" : ""}>${enviando ? "enviando…"
        : `Enviar${marcados.length ? ` (${marcados.length} item${marcados.length > 1 ? "s" : ""})` : ""}`}</button>
    </div>`;
  }

  // no painel só o que precisa de olho (vidro, avaria média/grave, foto fora do lugar); o resto fica na central de fotos
  function resumoFotos(v) {
    const fotos = res.fotos || [], fora = new Set((v?.fora_do_lugar || []).map(x => x.foto));
    const danos = fotos.reduce((n, f) => n + avariasDe(f).length + vidrosDe(f).length, 0);
    const linha = (cls, texto, rotulo, sub = "") => `<div class="lf ${cls}"><span>${esc(texto)}${sub ? `<small> · ${esc(sub)}</small>` : ""}</span>
      ${rotulo ? `<button data-acao="foto" data-rotulo="${esc(rotulo)}">ver</button>` : ""}</div>`;
    const linhas = v ? [
      ...(v.fora_do_lugar || []).map(x => linha("alerta", `Fora do lugar: o espaço "${x.foto}" recebeu ${x.e}`, x.foto)),
      ...v.vidros.map(d => linha("media", `Vidro · ${d.vidro}: ${d.tipo}`, d.foto)),
      ...v.avarias.map(a => linha(GRAV[a.gravidade] || "media", `Avaria · ${a.local}: ${a.tipo} (${a.gravidade})`, a.foto)),
      ...v.fotos_com_problema.filter(x => !fora.has(x.foto)).map(x => linha("", `Conferir foto: ${x.foto}`, x.foto, x.motivo)),
    ] : [];
    return `<h3>Fotos (IA)</h3><div class="item lista fotos-resumo">
      ${v ? `<div class="fonte">estado geral: <b>${esc(v.estado_geral || "-")}</b>${v.km?.length ? ` · km ${esc(v.km[0])}` : ""}</div>` : ""}
      <div class="fonte">${fotos.length} foto(s) · ${danos} dano(s) vistos pela IA${v?.leves ? ` (${v.leves} leve(s): só na central)` : ""}</div>
      ${linhas.join("")}
      ${fotos.length ? `<button class="pri abre-central" data-acao="central">Abrir central de fotos</button>` : ""}</div>`;
  }

  // ---------------- central de fotos: visualizador com zoom + o que a IA viu em cada foto ----------------
  const GRAV = {leve: "leve", media: "media", "média": "media", grave: "grave"};
  const DOCS = ["cnh", "crlv", "comprovante"];
  const grandes = new Map(), minis = new Map();  // rótulo → data: URL (limpos ao trocar de card)
  let central = null, arrasto = null;            // central: {i (em res.fotos), filtro, z, x, y, giro}
  const avariasDe = f => (f.analise?.avarias || []).filter(a => a.tipo);
  const vidrosDe = f => (f.analise?.vidros?.danos || []).filter(d => d.tipo);
  const naoBate = f => f.analise?.corresponde_ao_rotulo === false && !f.origem.startsWith("Anexo");  // anexo: descrição livre, não tem "espaço"
  const problemaDe = f => !!(f.fora_do_lugar || naoBate(f) || f.analise?.erro);
  const FILTROS = {
    todas: ["Todas", () => true],
    danos: ["Com dano", f => avariasDe(f).length + vidrosDe(f).length > 0],
    problema: ["Fora do lugar / problema", problemaDe],
    docs: ["Documentos", f => !!f.usada_como || DOCS.includes(f.lida_como) || DOCS.includes(f.analise?.tipo_real)],
  };
  const visiveis = () => res.fotos.filter(FILTROS[central.filtro][1]);

  function abrirCentral(rotulo) {
    if (!res?.fotos?.length) return aviso("Este resultado não tem a lista de fotos: clique em analisar de novo.");
    fecharPopover();
    central = {filtro: "todas", i: Math.max(0, res.fotos.findIndex(f => f.rotulo === rotulo)), z: 1, x: 0, y: 0, giro: 0};
    desenharCentral();
  }

  function fecharCentral() {
    central = arrasto = null;
    desenharCentral();
  }

  function irPara(passo) {
    const lista = visiveis();
    if (!lista.length) return;
    const pos = lista.indexOf(res.fotos[central.i]);
    central.i = res.fotos.indexOf(lista[pos < 0 ? 0 : (pos + passo + lista.length) % lista.length]);
    ajustar(false);
    central.giro = 0;
    desenharCentral();
  }

  function ajustar(aplicar = true) {
    Object.assign(central, {z: 1, x: 0, y: 0});
    if (aplicar) aplicarZoom();
  }

  // cx, cy: ponto do palco (a partir do centro) que fica parado enquanto amplia
  function zoom(fator, cx, cy) {
    const z = Math.min(8, Math.max(1, central.z * fator));
    if (z === 1) Object.assign(central, {x: 0, y: 0});
    else Object.assign(central, {x: cx - (cx - central.x) * z / central.z, y: cy - (cy - central.y) * z / central.z});
    central.z = z;
    aplicarZoom();
  }

  function aplicarZoom() {
    const img = raiz?.getElementById("c-img");
    if (!central || !img) return;
    let caber = 1;  // girada de lado, a foto deitada precisa diminuir para caber no palco
    if (central.giro % 180 && img.naturalWidth) {
      const W = img.clientWidth, H = img.clientHeight, r = img.naturalWidth / img.naturalHeight;
      const [dw, dh] = W / r > H ? [H * r, H] : [W, W / r];
      caber = Math.min(1, W / dh, H / dw);
    }
    img.style.transform = `translate(${central.x}px, ${central.y}px) scale(${central.z * caber}) rotate(${central.giro}deg)`;
    const t = raiz.getElementById("c-zoom");
    if (t) t.textContent = `${Math.round(central.z * 100)}%`;
    raiz.getElementById("palco")?.classList.toggle("ampliada", central.z > 1);
  }

  function origemHtml(f) {
    if (!f.origem.startsWith("Anexo")) return `<b>VISTORIA</b> enviada no espaço "${esc(f.rotulo)}"`;
    const d = f.data ? new Date(/^\d+$/.test(f.data) ? Number(f.data) : f.data) : null;
    return `<b>ANEXO</b> aba ${esc(f.origem.split("aba ")[1] || "Atividades")} · "${esc(f.rotulo.replace(/^Anexo \([^)]*\):\s*/i, ""))}"` +
           (d && !isNaN(d) ? ` · ${esc(d.toLocaleString("pt-BR"))}` : "");
  }

  // baixa a foto (grande ou miniatura) uma vez e encaixa onde ela já estiver desenhada
  function pedirFoto(rotulo, mini) {
    const mapa = mini ? minis : grandes, k = (mini ? "m:" : "g:") + rotulo, chave = res.codigo;
    if (mapa.has(rotulo) || pedindo.has(k)) return;
    pedindo.add(k);
    pedir({tipo: "imagem", chave, rotulo, mini}).then(r => {
      pedindo.delete(k);
      if (r.status !== 200 || chave !== res?.codigo) return;
      mapa.set(rotulo, r.dados);
      const i = res.fotos.findIndex(f => f.rotulo === rotulo);
      if (mini) raiz.querySelectorAll(`img[data-mi="${i}"]`).forEach(img => { img.src = r.dados; });
      else if (central?.i === i) {
        const img = raiz.getElementById("c-img");
        img.onload = aplicarZoom;
        img.src = r.dados;
        raiz.getElementById("c-carrega")?.remove();
      }
    });
  }

  function desenharCentral() {
    const el = raiz?.getElementById("central");
    if (!el) return;
    if (!central || !res?.fotos?.length) { el.hidden = true; el.innerHTML = ""; return; }
    const fotos = res.fotos, f = fotos[central.i], lista = visiveis(), pos = lista.indexOf(f);
    el.hidden = false;
    el.innerHTML = `
      <div class="c-topo"><div><b>Central de fotos</b> <span class="mono">${esc(res.placa)}</span> <small>${esc(res.veiculo)}</small></div>
        <div class="c-filtros">${Object.entries(FILTROS).map(([k, [t, fn]]) =>
          `<button class="${central.filtro === k ? "on" : ""}" data-acao="c-filtro" data-f="${k}">${t} <span>${fotos.filter(fn).length}</span></button>`).join("")}</div>
        <button class="x" data-acao="c-fechar" title="fechar (Esc)">×</button></div>
      <div class="c-meio">
        <div class="c-palco" id="palco">
          <img id="c-img" src="${grandes.get(f.rotulo) || ""}" alt="" draggable="false">
          ${grandes.has(f.rotulo) ? "" : `<div class="c-carrega" id="c-carrega"><span class="giro"></span> carregando a foto…</div>`}
          <div class="c-origem ${f.origem.startsWith("Anexo") ? "anexo" : ""}">${origemHtml(f)}</div>
          <div class="c-ctrl">
            <button data-acao="c-ant" title="anterior (←)">‹</button><span>${pos < 0 ? "fora do filtro" : `${pos + 1} de ${lista.length}`}</span>
            <button data-acao="c-prox" title="próxima (→)">›</button><i class="sep"></i>
            <button data-acao="c-menos" title="diminuir (−)">−</button><span id="c-zoom">100%</span><button data-acao="c-mais" title="aumentar (+)">+</button>
            <button data-acao="c-ajustar" title="ajustar à tela (0)">ajustar</button><button data-acao="c-girar" title="girar 90° (R)">girar</button>
          </div>
        </div>
        <div class="c-lado">${analiseHtml(f)}</div>
      </div>
      <div class="c-faixa">${lista.map(g => {
        const k = fotos.indexOf(g), n = avariasDe(g).length + vidrosDe(g).length;
        const pior = vidrosDe(g).length || avariasDe(g).some(a => GRAV[a.gravidade] === "media") ? "media" : "leve";
        return `<button class="c-mini ${k === central.i ? "atual" : ""} ${g.origem.startsWith("Anexo") ? "anexo" : ""}" data-acao="c-ir" data-i="${k}"
            title="${esc(g.origem)}: ${esc(g.rotulo)}"><img data-mi="${k}" src="${minis.get(g.rotulo) || ""}" alt="">
          ${problemaDe(g) ? `<span class="b alerta">!</span>` : ""}${n ? `<span class="b ${avariasDe(g).some(a => GRAV[a.gravidade] === "grave") ? "grave" : pior}">${n}</span>` : ""}
          ${g.usada_como ? `<span class="doc">DOC</span>` : ""}<span class="rot">${k + 1}. ${esc(g.rotulo)}</span></button>`;
      }).join("")}</div>`;
    raiz.getElementById("c-img").onload = aplicarZoom;  // girada de lado: o encaixe depende do tamanho real da foto
    pedirFoto(f.rotulo, false);
    lista.forEach(g => pedirFoto(g.rotulo, true));
    aplicarZoom();
    raiz.querySelector(".c-mini.atual")?.scrollIntoView({block: "nearest", inline: "center"});
  }

  // tudo o que a análise por foto devolveu, em blocos curtos (só o que a IA viu de fato)
  function analiseHtml(f) {
    const a = f.analise;
    const sec = (titulo, corpo) => corpo ? `<div class="c-sec"><h4>${titulo}</h4>${corpo}</div>` : "";
    const dano = (cls, onde, oque, grav, certeza) => `<div class="dano ${cls}"><b>${esc(onde)}</b> ${esc(oque)}
      <small>${grav ? esc(grav) : ""}${certeza ? ` · certeza ${esc(certeza)}` : ""}</small></div>`;
    let h = `<div class="c-rot">${esc(f.rotulo)}</div><div class="c-de">${origemHtml(f)}</div>`;
    if (f.fora_do_lugar) h += `<div class="alerta">Foto no lugar errado: o espaço "${esc(f.rotulo)}" recebeu ${esc(f.fora_do_lugar)}.</div>`;
    const doc = f.usada_como ? `Usada como <b>${esc(f.usada_como)}</b>: os dados lidos pela IA estão nos campos do painel.`
      : DOCS.includes(f.lida_como) ? `A leitura de documentos viu aqui: <b>${esc(f.lida_como)}</b> (outra foto foi a escolhida).` : "";
    if (!a) return h + sec("Documento", doc) + `<div class="c-sec"><small>Esta foto não passou pela análise da IA. Clique em "analisar de novo" no painel.</small></div>`;
    if (a.erro) return h + `<div class="alerta">A IA falhou nesta foto: ${esc(a.erro)}</div>` + sec("Documento", doc);
    if (naoBate(f) && !f.fora_do_lugar)
      h += `<div class="alerta">Não mostra o que o espaço pede${a.motivo_se_nao_corresponde ? `: ${esc(a.motivo_se_nao_corresponde)}` : ""}</div>`;
    h += sec("O que a IA vê", `${esc(a.o_que_a_foto_mostra || "-")}${a.tipo_real ? ` <span class="tag">${esc(a.tipo_real)}</span>` : ""}`);
    h += sec("Documento", doc);
    const av = avariasDe(f), vd = vidrosDe(f);
    h += sec(`Danos (${av.length + vd.length})`, av.length + vd.length
      ? [...vd.map(d => dano("media", `Vidro · ${d.vidro}`, `${d.tipo}${d.descricao ? ` — ${d.descricao}` : ""}`, "", d.certeza)),
         ...av.map(x => dano(GRAV[x.gravidade] || "leve", x.local, x.tipo, x.gravidade, x.certeza))].join("")
      : `<small>nenhum dano visto nesta foto</small>`);
    const vi = a.vidros || {};
    const vidros = vi.aparecem ? [["Para-brisa", vi.para_brisa], ["Laterais", vi.laterais], ["Traseiro", vi.traseiro], ["Retrovisores", vi.retrovisores],
      ["Faróis/lanternas", vi.farois_lanternas]].filter(([, t]) => t).map(([n, t]) => `<div><b>${n}:</b> ${esc(t)}</div>`).join("") : "";
    h += sec("Vidros", vidros);
    const pn = a.pneus || {};
    h += sec("Pneus", pn.aparece ? `${pn.estado ? `<span class="tag">${esc(pn.estado)}</span> ` : ""}${esc(pn.observacoes || "")}` : "");
    const pa = a.painel || {};
    h += sec("Painel", pa.aparece ? [pa.ligado != null && `${pa.ligado ? "ligado" : "desligado"}`, pa.km && `km <b>${esc(pa.km)}</b>`,
      pa.nivel_combustivel && `combustível ${esc(pa.nivel_combustivel)}`, pa.cambio_automatico != null && `câmbio ${pa.cambio_automatico ? "automático" : "manual"}`,
      pa.luzes_de_alerta_acesas?.length && `luzes acesas: ${esc(pa.luzes_de_alerta_acesas.join(", "))}`,
      pa.mensagens_na_tela?.length && `mensagens: ${esc(pa.mensagens_na_tela.join(", "))}`].filter(Boolean).join(" · ") : "");
    h += sec("Motor", a.motor?.aparece ? esc(a.motor.observacoes || "aparece na foto") : "");
    h += sec("Chassi gravado", a.chassi_visivel ? `<span class="mono">${esc(a.chassi_visivel)}</span>` : "");
    h += sec("Partes visíveis", (a.partes_visiveis || []).filter(p => p.parte).map(p =>
      `<div><b>${esc(p.parte)}</b> <span class="tag ${p.estado === "ruim" ? "grave" : p.estado === "regular" ? "media" : ""}">${esc(p.estado || "-")}</span> ${esc(p.observacao || "")}</div>`).join(""));
    const ve = a.veiculo || {};
    h += sec("Veículo nesta foto", [ve.marca, ve.modelo, ve.versao_ou_detalhes, ve.cor, ve.placa_visivel && `placa ${ve.placa_visivel}`].filter(Boolean).map(esc).join(" · "));
    h += sec("Estado geral", a.estado_geral && a.estado_geral !== "nao_da_para_avaliar"
      ? `<span class="tag ${a.estado_geral === "ruim" ? "grave" : a.estado_geral === "regular" ? "media" : ""}">${esc(a.estado_geral)}</span> ${esc(a.estado_geral_descricao || "")}` : "");
    h += sec("Pontos de atenção", (a.pontos_de_atencao || []).map(p => `<div>• ${esc(p)}</div>`).join(""));
    const q = a.qualidade_da_foto || {};
    h += sec("Qualidade da foto", [q.nitida === false ? "sem nitidez" : "nítida", q.iluminacao && `iluminação ${q.iluminacao}`,
      ...(q.problemas || []), a.confianca && `confiança da IA: ${a.confianca}`].filter(Boolean).map(esc).join(" · "));
    return h + `<div class="c-pe">Leitura da IA: confira sempre olhando a foto (reflexo e sujeira enganam).</div>`;
  }

  // olho escaneando enquanto o servidor analisa o card (dorme se estiver na fila)
  function analisando(s) {
    const forma = "M4 26 Q38 -4 72 26 Q38 56 4 26Z", fila = s.estado === "fila";
    const pct = !s.fotos ? 8 : Math.min(96, 30 + 60 * (s.vistas || 0) / Math.max(1, s.fotos - 3));
    return `<div class="escaneia ${fila ? "dorme" : ""}">
      <svg class="olho" viewBox="0 0 76 52"><defs><clipPath id="asx-cl"><path d="${forma}"/></clipPath></defs><path class="esclera" d="${forma}"/>
        <g clip-path="url(#asx-cl)"><g class="iris"><circle cx="38" cy="26" r="13" fill="#3157d5"/><circle cx="38" cy="26" r="6" fill="#0b1020"/>
        <circle cx="42" cy="21" r="2.6" fill="#fff"/></g><rect class="palpebra" x="0" y="0" width="76" height="52"/></g>
        <g class="zzz"><text x="60" y="12">z</text><text x="66" y="6">z</text><text x="72" y="0">z</text></g></svg>
      <b>${fila ? "Na fila: o olho acorda quando chegar a vez" : "A IA está escaneando este card"}</b>
      ${fila && s.posicao ? `<small>${s.posicao === 1 ? "é o próximo" : `${s.posicao - 1} card(s) na frente`}${s.rodando_agora
        ? ` · agora: ${esc(s.rodando_agora.placa || s.rodando_agora.pedido)}${s.rodando_agora.por?.length ? ` (de ${esc(s.rodando_agora.por.join(", "))})` : ""}` : ""}</small>` : ""}
      ${s.por?.length ? `<small>pedido por ${esc(s.por.join(", "))}</small>` : ""}
      <small>${esc(fila ? "" : s.etapa || "")}${s.fotos ? ` · ${s.vistas || 0} de ${s.fotos} fotos olhadas` : ""}${s.lidos ? ` · ${s.lidos} arquivo(s) lido(s)` : ""}</small>
      <div class="barrinha"><div style="width:${fila ? 3 : pct}%"></div></div>
      ${fila ? "" : imagensDoCard(s)}</div>`;
  }

  // ---------------- imagens do próprio card durante a análise (telas do CRM e fotos baixadas) ----------------
  const DOCUMENTO = /documento|cnh|crlv|comprovante|selfie|anexo|chassi/i;
  const PESSOAL = /documento cnh|comprovante/i;  // a IA das fotos não olha CNH e comprovante (só o OCR local)
  const imagens = new Map(), pedindo = new Set();  // "tipo/pedido/início/i" → data: URL
  let vivo = null, vivoIdx = 0;                    // imagem grande: o que o robô está olhando agora

  function faseDe(s) {
    if (s.fase === "documentos") return 5;
    if (s.fase !== "analise") return 0;
    const e = s.etapa || "";
    return /tabela|compara|Confer|Resultado/i.test(e) ? 4 : /FIPE/i.test(e) ? 3 : /fotos/i.test(e) ? 2 : 1;
  }

  function estadoFoto(r, f, vistas, lidos) {
    if (f === 0) return "baixada";
    if (f === 5 && DOCUMENTO.test(r)) return "escaneando";
    if (f >= 3) return vistas.has(r) ? "visto" : lidos.has(r) ? "lido" : "visto";
    if (f === 1) return lidos.has(r) ? "lido" : DOCUMENTO.test(r) ? "escaneando" : "espera";
    return vistas.has(r) ? "visto" : lidos.has(r) ? "lido" : PESSOAL.test(r) ? "espera" : "escaneando";
  }

  // devolve a imagem se já chegou; senão pede ao servidor e encaixa nos <img data-k> quando chegar
  function imagem(s, tipo, i, tentativa = 0) {
    const k = `${tipo}/${s.pedido}/${s.inicio}/${i}`;
    if (imagens.has(k)) return imagens.get(k);
    if (!pedindo.has(k)) {
      pedindo.add(k);
      pedir({tipo, pedido: s.pedido, i}).then(r => {
        pedindo.delete(k);
        if (r.status === 200) {
          imagens.set(k, r.dados);
          raiz?.querySelectorAll(`img[data-k="${k}"]`).forEach(img => { img.src = r.dados; });
        } else if (tentativa < 4) setTimeout(() => imagem(s, tipo, i, tentativa + 1), 1500);  // a foto pode estar sendo gravada
      });
    }
    return "";
  }

  function imagensDoCard(s) {
    if (!s.pedido || !s.nomes_fotos) { vivo = null; return ""; }  // servidor antigo: sem imagens
    const f = faseDe(s), fotos = s.nomes_fotos, vistas = new Set(s.nomes_vistos), lidos = new Set(s.nomes_lidos);
    const estados = fotos.map(r => estadoFoto(r, f, vistas, lidos));
    // no CRM: a última tela fotografada; depois: sorteia entre as fotos sendo escaneadas
    const itens = f === 0 && s.telas?.length ? [{tipo: "tela", i: s.telas.length - 1, rotulo: s.telas[s.telas.length - 1]}]
      : fotos.map((r, i) => ({tipo: "miniatura", i, rotulo: r})).filter(x => estados[x.i] === "escaneando");
    const lista = itens.length ? itens : fotos.map((r, i) => ({tipo: "miniatura", i, rotulo: r}));
    vivo = lista.length ? {s, itens: lista} : null;
    const atual = vivo && vivo.itens[vivoIdx % vivo.itens.length];
    const destaque = atual ? `<div class="destaque ${atual.tipo}"><img id="destaque-img" data-k="${atual.tipo}/${s.pedido}/${s.inicio}/${atual.i}"
        src="${imagem(s, atual.tipo, atual.i)}" alt=""><div class="feixe"></div><div class="legenda" id="destaque-rot">${legendaDe(atual)}</div></div>` : "";
    const grade = fotos.length ? `<div class="grade">${fotos.map((r, i) => `<div class="mini ${estados[i]}${/anexo/i.test(r) ? " anexo" : ""}"
        title="${origemCurta(r)}: ${esc(r)}">
        <img data-k="miniatura/${s.pedido}/${s.inicio}/${i}" src="${imagem(s, "miniatura", i)}" alt=""></div>`).join("")}</div>`
      : `<div class="fotinhas">${Array.from({length: 6}, (_, i) => `<i style="--a:${i * .25}s"></i>`).join("")}</div>`;
    return destaque + grade;
  }

  // troca a imagem grande a cada 1,6 s (só ela: o resto do painel não pisca)
  setInterval(() => {
    const img = raiz?.getElementById("destaque-img");
    if (!vivo || !img || vivo.itens.length < 2) return;
    vivoIdx = (vivoIdx + 1 + Math.floor(Math.random() * (vivo.itens.length - 1))) % vivo.itens.length;
    const x = vivo.itens[vivoIdx], k = `${x.tipo}/${vivo.s.pedido}/${vivo.s.inicio}/${x.i}`;
    img.dataset.k = k;
    img.src = imagem(vivo.s, x.tipo, x.i);
    raiz.getElementById("destaque-rot").innerHTML = legendaDe(x);
  }, 1600);

  // de onde veio a imagem que está passando: tela do CRM, foto da vistoria ou anexo (abas Atividades, Cotações, Contato)
  const legendaDe = x => `<b>${x.tipo === "tela" ? "TELA DO CRM" : origemCurta(x.rotulo).toUpperCase()}</b> ${esc(x.rotulo)}`;
  const origemCurta = r => /anexo.{0,3}cota/i.test(r) ? "Anexo · Cotações" : /anexo.{0,3}contato/i.test(r) ? "Anexo · Contato"
    : /anexo/i.test(r) ? "Anexo · Atividades" : "Vistoria";

  // ---------------- popover do selo, foto e aviso ----------------
  function abrirPopover(c, selo, manter = false) {
    if (!raiz) montarRaiz();
    const pop = raiz.getElementById("pop");
    if (!manter && popoverDe === selo && !pop.hidden) return fecharPopover();
    popoverDe = selo;
    pop.innerHTML = `<button class="x" data-acao="fecharpop">×</button>${itemCampo(c)}
      <div class="sub">${c.nome === "vhclFipeCode" && c.status !== "ok" ? "O robô não troca o código FIPE: só sugere." : ""}</div>
      ${c.encontrado && c.encontrado !== "não encontrado" && c.status !== "ok" ? `<button data-acao="copiar" data-nome="${esc(c.nome)}">copiar valor</button>` : ""}`;
    const r = selo.getBoundingClientRect();
    pop.hidden = false;
    pop.style.left = `${Math.max(8, Math.min(r.left, innerWidth - 360))}px`;
    pop.style.top = `${r.bottom + 6 + pop.offsetHeight > innerHeight ? Math.max(8, r.top - 6 - pop.offsetHeight) : r.bottom + 6}px`;
  }

  function fecharPopover() {
    popoverDe = null;
    if (raiz) raiz.getElementById("pop").hidden = true;
  }

  async function verFoto(rotulo) {
    const z = raiz.getElementById("zoom");
    z.hidden = false;
    z.innerHTML = `<div class="vazio" style="color:#fff"><span class="giro"></span> carregando ${esc(rotulo)}…</div>`;
    const r = await pedir({tipo: "imagem", chave: res.codigo, rotulo});
    z.innerHTML = r.status === 200 ? `<figure data-acao="fecharzoom"><img src="${r.dados}" alt=""><figcaption>${esc(rotulo)} · clique ou Esc para fechar</figcaption></figure>`
      : `<div class="vazio" data-acao="fecharzoom" style="color:#fff">foto indisponível · clique para fechar</div>`;
  }

  let tempoAviso;
  function aviso(texto, bom = false) {
    if (!raiz) montarRaiz();
    const t = raiz.getElementById("toast");
    t.className = bom ? "bom" : "";
    t.textContent = texto;
    t.hidden = false;
    clearTimeout(tempoAviso);
    tempoAviso = setTimeout(() => { t.hidden = true; }, 5000);
  }

  // com a central aberta o teclado é dela (e o Esc não chega ao CRM, que fecharia o card)
  window.addEventListener("keydown", ev => {
    if (!central) return;
    const acao = {Escape: fecharCentral, ArrowLeft: () => irPara(-1), ArrowRight: () => irPara(1), "+": () => zoom(1.4, 0, 0),
                  "=": () => zoom(1.4, 0, 0), "-": () => zoom(1 / 1.4, 0, 0), "0": () => ajustar(),
                  r: () => { central.giro = (central.giro + 90) % 360; aplicarZoom(); }}[ev.key.length === 1 ? ev.key.toLowerCase() : ev.key];
    if (!acao) return;
    ev.preventDefault();
    ev.stopPropagation();
    acao();
  }, true);
  document.addEventListener("keydown", ev => {
    if (ev.key !== "Escape" || !raiz) return;
    raiz.getElementById("zoom").hidden = true;
    fecharPopover();
  });
  document.addEventListener("click", ev => { if (popoverDe && !ev.composedPath().some(e => e.id === "asx-assistente" || e === popoverDe)) fecharPopover(); }, true);

  const CSS_PAINEL = `
    :host{--txt:#1b2333;--sub:#667085;--borda:#e3e7ef;--card:#fff;--acento:#3157d5;--acentof:#e8eefc;--ia:#7c3aed}
    *{box-sizing:border-box;font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
    .painel{position:fixed;top:64px;right:12px;width:370px;max-height:calc(100vh - 80px);display:flex;flex-direction:column;background:var(--card);color:var(--txt);
      border:1px solid var(--borda);border-radius:14px;box-shadow:0 12px 40px rgba(16,24,40,.22);font-size:13px;line-height:1.4;animation:entra .25s ease-out}
    @keyframes entra{from{opacity:0;transform:translateX(20px)}}
    .topo{display:flex;justify-content:space-between;gap:8px;align-items:flex-start;padding:12px 14px;border-bottom:1px solid var(--borda);
      background:linear-gradient(135deg,#eef3ff,#f6f0ff);border-radius:14px 14px 0 0}
    .titulo .pill{margin-left:6px}
    .tudo{margin:10px 14px 0;padding:9px 12px;border:0;border-radius:10px;color:#fff;font-size:13px;font-weight:700;cursor:pointer;
      background:linear-gradient(90deg,#3157d5,#7c3aed,#3157d5);background-size:200% 100%;animation:corre 3s linear infinite;
      display:flex;justify-content:center;gap:8px;align-items:center}
    .tudo span{background:rgba(255,255,255,.25);border-radius:999px;padding:0 8px;font-size:12px}
    .tudo:hover{filter:brightness(1.1);color:#fff}
    @keyframes corre{to{background-position:-200% 0}}
    .corpo{overflow:auto;padding:10px 14px 14px;flex:1 1 auto;min-height:0}
    .retorno-abrir{flex-shrink:0;margin:0;border:0;border-top:1px solid var(--borda);border-radius:0 0 14px 14px;padding:10px 14px;
      background:#fafbfd;color:var(--acento);font-size:12.5px;text-align:left}
    .retorno-abrir:hover{background:var(--acentof)}
    .retorno{flex-shrink:0;max-height:48%;overflow:auto;display:flex;flex-direction:column;gap:6px;padding:10px 14px 12px;
      border-top:2px solid #f3b4b6;background:#fff8f8;border-radius:0 0 14px 14px;box-shadow:0 -6px 14px rgba(16,24,40,.06)}
    .retorno>.linha b{color:#c0262d;font-size:12.5px}
    .etqs{display:flex;flex-wrap:wrap;gap:5px}
    .etq{display:inline-flex;align-items:center;gap:5px;max-width:100%;padding:2px 4px 2px 9px;border-radius:999px;background:#fde8e8;
      color:#9b1c1c;font-size:12px;border:1px solid #f3b4b6}
    .etq small{color:#b4424a;font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:140px}
    .etq button{border:0;background:transparent;color:#9b1c1c;padding:0 5px;font-size:14px;line-height:1}
    .retorno .enviar{padding:7px 10px;font-size:12.5px}
    .retorno .enviar:disabled{opacity:.6;cursor:default}
    .marca{font-weight:600}.item.errado .marca{color:#c0262d}
    .sub,small{color:var(--sub);font-size:12px}.mono{font-family:ui-monospace,Consolas,monospace}
    h3{font-size:11.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--sub);margin:16px 0 6px}
    .ok{--c:#14804a;--f:#e5f6ec}.sugerido{--c:#8a4f00;--f:#fff3dc}.conferir{--c:#c2410c;--f:#fff1e6}.diverge{--c:#c0262d;--f:#fde8e8}.preenchido{--c:#7c3aed;--f:#f1eafe}
    .pill{display:inline-flex;align-items:center;gap:5px;padding:1px 9px;border-radius:999px;font-size:11.5px;font-weight:600;white-space:nowrap;background:var(--f,#eef0f4);color:var(--c,var(--sub))}
    i{display:inline-block;width:7px;height:7px;border-radius:50%;background:var(--c,var(--borda));flex-shrink:0}
    .rot i{margin-right:6px;vertical-align:1px}
    .contagem{display:flex;flex-wrap:wrap;gap:5px}
    .item{border:1px solid var(--borda);border-left:3px solid var(--c,var(--borda));border-radius:9px;padding:7px 9px;margin:6px 0;background:#fff}
    .item.sugerido{background:#fffaf0}.item.preenchido{background:#faf7ff}
    .item.errado{background:#fff5f5;border-color:#f3b4b6;border-left-color:#c0262d}
    .item.lista>div{margin:3px 0}
    .linha{display:flex;justify-content:space-between;gap:6px;align-items:center}
    .rot{font-weight:600}.rot small{font-weight:400;margin-left:2px}
    .valor{font:600 14px ui-monospace,Consolas,monospace;margin:3px 0;word-break:break-all}
    .de-para{display:flex;flex-direction:column;font-size:12px;margin:3px 0}
    .fonte{font-size:11.5px;color:var(--sub)}
    .marca{display:inline-flex;gap:5px;align-items:center;font-size:11.5px;color:var(--sub);margin-top:4px;cursor:pointer}
    .marca input{margin:0}
    details{font-size:12px;color:var(--sub);margin-top:3px}summary{cursor:pointer}
    details.bloco{margin-top:12px;color:var(--txt)}details.bloco>summary{font-weight:600}
    button{font:600 11.5px system-ui,sans-serif;border:1px solid var(--borda);background:#fff;color:var(--txt);border-radius:7px;padding:3px 8px;cursor:pointer}
    button:hover{border-color:var(--acento);color:var(--acento)}
    button.pri{background:var(--acento);border-color:var(--acento);color:#fff}button.pri:hover{filter:brightness(1.1);color:#fff}
    .botoes{display:flex;gap:4px;flex-shrink:0}
    button.x{border:0;background:transparent;font-size:18px;line-height:1;padding:0 6px;color:var(--sub)}
    .alerta{margin:8px 0 0;padding:8px 10px;border-radius:9px;background:#fff1e6;color:#9a3412;border:1px solid #fed7aa;font-size:12.5px}
    .salvar{margin:10px 0 4px;padding:8px 10px;border-radius:9px;background:#f1eafe;color:var(--ia);font-size:12.5px}
    .obs{border:1px solid var(--borda);border-radius:10px;padding:8px;display:flex;flex-direction:column;gap:6px;background:#fafbfd}
    textarea,input:not([type]){width:100%;font:13px system-ui,sans-serif;border:1px solid var(--borda);border-radius:8px;padding:6px 8px;color:var(--txt);background:#fff;resize:vertical}
    textarea:focus,input:focus{outline:2px solid var(--acentof);border-color:var(--acento)}
    .obs .linha input{flex:1}.obs .linha button{flex-shrink:0;padding:6px 10px}
    .anterior{border-top:1px solid var(--borda);padding:5px 0;color:var(--txt)}
    .vazio{text-align:center;padding:22px 8px;color:var(--sub)}.vazio button{margin-top:10px}
    .rodape{margin-top:14px;padding-top:10px;border-top:1px solid var(--borda);font-size:11.5px;color:var(--sub)}
    .giro{display:inline-block;width:12px;height:12px;border:2px solid currentColor;border-right-color:transparent;border-radius:50%;animation:gira .8s linear infinite;vertical-align:-2px}
    @keyframes gira{to{transform:rotate(360deg)}}
    .bolha{position:fixed;right:16px;bottom:16px;padding:9px 14px;border-radius:999px;font-size:13px;background:var(--acento);color:#fff;border:0;box-shadow:0 8px 24px rgba(16,24,40,.25)}
    .bolha:hover{color:#fff;filter:brightness(1.1)}
    #pop{position:fixed;width:350px;background:#fff;border:1px solid var(--borda);border-radius:12px;padding:8px 10px 10px;box-shadow:0 12px 40px rgba(16,24,40,.25);
      font-size:13px;color:var(--txt)}#pop .x{float:right}
    #toast{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);max-width:560px;padding:10px 16px;border-radius:10px;background:#c0262d;color:#fff;
      font-size:13px;box-shadow:0 8px 24px rgba(16,24,40,.3)}#toast.bom{background:#1b2333}
    #zoom{position:fixed;inset:0;background:rgba(10,14,22,.82);display:flex;align-items:center;justify-content:center;width:100vw;height:100vh}
    #zoom[hidden],#pop[hidden],#toast[hidden]{display:none}
    figure{margin:0;text-align:center;cursor:zoom-out}img{max-width:92vw;max-height:84vh;border-radius:8px}figcaption{color:#fff;margin-top:8px;font-size:13px}
    .escaneia{display:flex;flex-direction:column;align-items:center;gap:8px;padding:18px 6px;text-align:center}
    .escaneia small{color:var(--sub)}
    .olho{width:84px;height:58px;overflow:visible}.olho .esclera{fill:#fff;stroke:#3157d5;stroke-width:2.5}
    .olho .iris{animation:olha 3.4s ease-in-out infinite}.olho .palpebra{fill:#cbd6f2;transform:translateY(-44px);animation:pisca 4.2s infinite}
    .olho .zzz{display:none}.dorme .olho .palpebra{animation:none;transform:translateY(-8px)}.dorme .olho .iris{animation:none}.dorme .zzz{display:block}
    .zzz text{font:700 11px system-ui;fill:#98a2b3;animation:sobe 2.4s ease-in infinite;opacity:0}.zzz text:nth-child(2){animation-delay:.8s}.zzz text:nth-child(3){animation-delay:1.6s}
    @keyframes olha{0%,100%{transform:translateX(-9px)}25%{transform:translate(0,-3px)}50%{transform:translateX(9px)}75%{transform:translate(0,3px)}}
    @keyframes pisca{0%,88%,100%{transform:translateY(-44px)}92%{transform:translateY(0)}96%{transform:translateY(-44px)}}
    @keyframes sobe{0%{opacity:0;transform:translate(0,0)}30%{opacity:1}100%{opacity:0;transform:translate(10px,-16px)}}
    .fotinhas{display:flex;gap:5px}.fotinhas i{width:34px;height:26px;border-radius:5px;background:#dfe5f1;position:relative;overflow:hidden}
    .fotinhas i::after{content:"";position:absolute;left:0;right:0;height:40%;top:-40%;background:linear-gradient(180deg,transparent,rgba(56,189,248,.8) 75%,#fff 78%,transparent 80%);
      animation:varre 1.5s linear infinite;animation-delay:var(--a)}
    .dorme .fotinhas i::after{display:none}
    @keyframes varre{to{top:100%}}
    .destaque{position:relative;width:100%;aspect-ratio:4/3;border-radius:10px;overflow:hidden;background:#0b1020}
    .destaque.tela{aspect-ratio:16/10;background:#eef0f4}
    .destaque img{width:100%;height:100%;max-width:none;max-height:none;object-fit:contain;border-radius:0;display:block}
    .destaque .feixe{position:absolute;left:0;right:0;height:30%;top:-30%;background:linear-gradient(180deg,transparent,rgba(56,189,248,.45) 85%,#fff 88%,transparent 90%);
      animation:varre 1.6s linear infinite}
    .destaque .legenda{position:absolute;left:0;right:0;bottom:0;padding:4px 8px;font-size:11.5px;color:#fff;background:rgba(11,16,32,.7);text-align:left;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .grade{display:grid;grid-template-columns:repeat(5,1fr);gap:4px;width:100%;max-height:156px;overflow:auto}
    .mini{position:relative;aspect-ratio:4/3;border-radius:5px;overflow:hidden;background:#dfe5f1}
    .mini img{width:100%;height:100%;max-width:none;max-height:none;object-fit:cover;border-radius:0;display:block}
    img[src=""]{visibility:hidden}
    .mini.espera img{opacity:.4;filter:grayscale(1)}
    .mini.escaneando::after{content:"";position:absolute;left:0;right:0;height:40%;top:-40%;background:linear-gradient(180deg,transparent,rgba(56,189,248,.8) 75%,#fff 78%,transparent 80%);
      animation:varre 1.5s linear infinite}
    .mini.visto::before,.mini.lido::before{position:absolute;right:2px;top:2px;z-index:1;width:14px;height:14px;border-radius:50%;color:#fff;font:700 9px/14px system-ui;text-align:center}
    .mini.visto::before{content:"✓";background:#14804a}.mini.lido::before{content:"✓";background:#3157d5}
    .barrinha{width:100%;height:5px;border-radius:999px;background:#eef0f4;overflow:hidden}
    .barrinha>div{height:100%;background:linear-gradient(90deg,#3157d5,#7c3aed);transition:width .6s}
    .destaque .legenda b,.c-origem b{font-size:10px;letter-spacing:.06em;color:#93b4ff;margin-right:5px}
    .mini.anexo{box-shadow:inset 0 3px 0 #a855f7}
    .fotos-resumo .lf{display:flex;justify-content:space-between;gap:6px;align-items:center;margin:4px 0;padding:3px 0 3px 8px;border-left:3px solid #d0d5dd}
    .lf.alerta{border-left-color:#ea580c}.lf.media{border-left-color:#d97706}.lf.grave{border-left-color:#c0262d}.lf.leve{border-left-color:#ca8a04}
    .lf button{flex-shrink:0}
    .abre-central{width:100%;margin-top:8px;padding:7px 10px;font-size:12.5px}
    /* central de fotos: palco (zoom) à esquerda, o que a IA viu à direita, miniaturas embaixo */
    #central{position:fixed;inset:0;width:100vw;height:100vh;display:grid;grid-template-rows:auto 1fr auto;background:#0b1020;color:#e8ecf5;font-size:13px}
    #central[hidden]{display:none}
    .c-topo{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 14px;border-bottom:1px solid #1f2940}
    .c-topo small{color:#98a2b3}
    .c-filtros{display:flex;gap:6px;flex-wrap:wrap;margin-left:auto}
    .c-filtros button{background:#141b2e;border-color:#2a3550;color:#cfd6e6;padding:4px 10px}
    .c-filtros button.on{background:#3157d5;border-color:#3157d5;color:#fff}
    .c-filtros span{opacity:.7;margin-left:2px}
    #central button.x{color:#cfd6e6;font-size:26px}
    .c-meio{display:grid;grid-template-columns:minmax(0,1fr) 390px;min-height:0}
    .c-palco{position:relative;overflow:hidden;background:#05070d;cursor:zoom-in;touch-action:none;user-select:none}
    .c-palco.ampliada{cursor:grab}.c-palco.arrastando{cursor:grabbing}
    #c-img{position:absolute;inset:0;width:100%;height:100%;max-width:none;max-height:none;object-fit:contain;border-radius:0;transform-origin:center;transition:transform .12s ease-out}
    .arrastando #c-img{transition:none}
    .c-carrega{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;gap:8px;color:#98a2b3}
    .c-origem{position:absolute;top:10px;left:10px;max-width:calc(100% - 20px);padding:6px 10px;border-radius:7px;background:rgba(11,16,32,.85);border-left:3px solid #3157d5}
    .c-origem.anexo{border-left-color:#a855f7}.c-origem.anexo b{color:#d8b4fe}
    .c-ctrl{position:absolute;left:50%;bottom:12px;transform:translateX(-50%);display:flex;align-items:center;gap:6px;padding:6px 8px;border-radius:10px;
      background:rgba(11,16,32,.88);white-space:nowrap}
    .c-ctrl button{background:#1b2440;border-color:#2a3550;color:#e8ecf5;min-width:30px;padding:4px 9px;font-size:13px}
    .c-ctrl button:hover,.c-filtros button:hover{border-color:#93b4ff;color:#fff}
    .c-ctrl span{min-width:44px;text-align:center;font-variant-numeric:tabular-nums;color:#cfd6e6}
    .c-ctrl .sep{width:1px;height:18px;background:#2a3550;border-radius:0}
    .c-lado{overflow:auto;padding:14px 16px 18px;background:#fff;color:var(--txt)}
    .c-rot{font-size:15px;font-weight:700}.c-de{font-size:12px;color:var(--sub);margin:2px 0 8px}.c-de b{color:var(--acento);font-size:10px;letter-spacing:.06em;margin-right:4px}
    .c-sec{border-top:1px solid var(--borda);padding:8px 0}.c-sec h4{margin:0 0 4px;font-size:11px;text-transform:uppercase;letter-spacing:.05em;color:var(--sub)}
    .c-sec>div{margin:3px 0}
    .tag{display:inline-block;padding:0 7px;border-radius:999px;background:#e5f6ec;color:#14804a;font-size:11px;font-weight:600}
    .tag.media{background:#fff3dc;color:#8a4f00}.tag.grave{background:#fde8e8;color:#c0262d}
    .dano{border-left:3px solid #ca8a04;background:#fffbea;border-radius:6px;padding:5px 8px;margin:4px 0}
    .dano.media{border-left-color:#d97706;background:#fff4e5}.dano.grave{border-left-color:#c0262d;background:#fdecec}
    .dano small{display:block}
    .c-pe{margin-top:10px;font-size:11.5px;color:var(--sub)}
    .c-faixa{display:flex;gap:6px;overflow-x:auto;padding:8px 12px 10px;border-top:1px solid #1f2940}
    .c-mini{position:relative;flex:0 0 112px;height:80px;padding:0;border:2px solid transparent;box-shadow:inset 0 3px 0 #3157d5;border-radius:7px;overflow:hidden;background:#141b2e}
    .c-mini.anexo{box-shadow:inset 0 3px 0 #a855f7}
    .c-mini.atual{border-color:#fff}
    .c-mini:hover{border-color:#93b4ff}
    .c-mini img{width:100%;height:100%;max-width:none;max-height:none;object-fit:cover;border-radius:0;display:block}
    .c-mini .rot{position:absolute;left:0;right:0;bottom:0;padding:1px 5px;background:rgba(0,0,0,.7);color:#fff;font:500 10px system-ui;text-align:left;
      white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
    .c-mini .b{position:absolute;top:5px;min-width:17px;height:17px;padding:0 4px;border-radius:9px;color:#fff;font:700 10px/17px system-ui;text-align:center}
    .c-mini .b.alerta{left:4px;background:#ea580c}.c-mini .b.leve{right:4px;background:#ca8a04}.c-mini .b.media{right:4px;background:#d97706}
    .c-mini .b.grave{right:4px;background:#c0262d}
    .c-mini .doc{position:absolute;right:4px;bottom:17px;padding:0 4px;border-radius:4px;background:#14804a;color:#fff;font:700 9px/14px system-ui}
    @media (max-width:900px){.c-meio{grid-template-columns:1fr;grid-template-rows:minmax(0,1fr) 40vh}}
    @media (prefers-reduced-motion:reduce){.painel,.giro,.tudo,.olho *,.fotinhas i::after,.zzz text,.destaque .feixe,.mini::after{animation:none}#c-img{transition:none}}`;

  // ---------------- selo em cima de cada card do funil ----------------
  // O card do funil é <section id="<código>" class="pwrcrm-card">. Pergunta ao servidor a situação dos que estão na tela
  // (a cada 8 s) e redesenha o selo a cada segundo (o CRM refaz os cards quando rola a coluna ou filtra).
  const SELO_SIT = {OK: ["asx-f-ok", "confere"], VERIFICAR: ["asx-f-conferir", "conferir"], DIVERGENTE: ["asx-f-diverge", "divergente"]};
  let situacoesFunil = {}, ultimaBusca = 0, buscando = false;
  const quando = t => {
    const d = new Date(t * 1000), hoje = new Date().toDateString() === d.toDateString();
    return (hoje ? "" : d.toLocaleDateString("pt-BR", {day: "2-digit", month: "2-digit"}) + " ") +
      d.toLocaleTimeString("pt-BR", {hour: "2-digit", minute: "2-digit"});
  };

  function textoSelo(s) {
    if (s.estado === "rodando") return ["asx-f-rodando", "⏳ Robô analisando agora"];
    if (s.estado === "fila") return ["asx-f-fila", "Na fila do robô"];
    if (s.estado === "erro") return ["asx-f-erro", "Análise do robô deu erro"];
    if (s.estado !== "pronto") return null;
    const [cls, sit] = SELO_SIT[s.situacao] || ["asx-f-ok", ""];
    return [cls, `✓ Analisado ${s.fim ? quando(s.fim) : ""}${sit ? ` · ${sit}` : ""}${s.sugeridos ? ` · ${s.sugeridos} sugest${s.sugeridos > 1 ? "ões" : "ão"}` : ""}`];
  }

  async function buscarSituacoes(codigos) {
    if (buscando) return;
    buscando = true;
    try {
      const novo = {};
      for (let i = 0; i < codigos.length; i += 150) {
        const r = await pedir({tipo: "situacoes", codigos: codigos.slice(i, i + 150)});
        if (r.status !== 200) return;  // servidor fora/antigo: fica sem selo (não atrapalha o CRM)
        Object.assign(novo, r.dados);
      }
      situacoesFunil = novo;
      ultimaBusca = Date.now();
    } finally {
      buscando = false;
    }
  }

  function selosNoFunil() {
    const cards = [...document.querySelectorAll("section.pwrcrm-card[id]")].filter(s => /^[A-Za-z0-9]{6,}$/.test(s.id));
    if (!cards.length) return;
    if (Date.now() - ultimaBusca > 8000) buscarSituacoes(cards.map(s => s.id));
    for (const s of cards) {
      const info = situacoesFunil[s.id] ? textoSelo(situacoesFunil[s.id]) : null;
      let selo = s.querySelector(":scope > .asx-funil");
      if (!info) { selo?.remove(); continue; }
      if (!selo) {
        selo = document.createElement("div");
        selo.className = "asx-funil";
        s.prepend(selo);
      }
      const [cls, texto] = info;
      if (selo.textContent !== texto) selo.textContent = texto;
      if (!selo.classList.contains(cls)) selo.className = "asx-funil " + cls;
    }
  }

  setInterval(ciclo, 1000);
  setInterval(selosNoFunil, 1000);
})();
