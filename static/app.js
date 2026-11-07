// WimsKrant — voorpagina en leesweergave; tik op een onderstreept woord voor uitleg in een popup.
// Wat het artikel eerder vertelde over een persoon of begrip, staat klaar als het later weer genoemd wordt.

const SOORTNAAM = { persoon: "Persoon", plaats: "Plaats", organisatie: "Organisatie", begrip: "Woord" };
const KOPPEN = { persoon: "Personen", plaats: "Plaatsen", organisatie: "Organisaties", begrip: "Woorden en begrippen" };
const $ = (id) => document.getElementById(id);

function veilig(t) {
  return String(t ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function bewaar(sleutel, waarde) { try { localStorage.setItem(sleutel, JSON.stringify(waarde)); } catch {} }
function lees(sleutel, standaard) {
  try { return JSON.parse(localStorage.getItem(sleutel)) ?? standaard; } catch { return standaard; }
}

const leesLink = (url) => "/lees?url=" + encodeURIComponent(url);

// Is de toegang verlopen (of de code ingetrokken), dan eerst opnieuw de code vragen.
const echteFetch = window.fetch.bind(window);
window.fetch = async (...args) => {
  const r = await echteFetch(...args);
  if (r.status === 401) location.href = "/toegang?terug=" + encodeURIComponent(location.pathname + location.search);
  return r;
};

// ---------- Startscherm: kies een krant ----------

const KRANTEN = { nrc: "NRC", trouw: "Trouw" };

function toonKeuze() {
  $("keuze").hidden = false;
  $("dag").textContent = new Date().toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  const eerder = lees("wk-eerder", []);
  if (eerder.length) {
    $("eerder").hidden = false;
    $("eerder-lijst").innerHTML = eerder.map((e) => {
      const wanneer = e.op ? new Date(e.op).toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long" }) : "";
      const krant = e.krant || (e.url.includes("trouw.nl") ? "trouw" : e.url.includes("nrc.nl") ? "nrc" : "");
      const over = [KRANTEN[krant], wanneer && "gelezen op " + wanneer].filter(Boolean).join(" · ");
      return `<li><a href="${leesLink(e.url)}"><span class="eerder-titel">${veilig(e.titel)}</span>${
        over ? `<span class="eerder-over">${veilig(over)}</span>` : ""}</a></li>`;
    }).join("");
  }
  // Per krant alvast het bovenste artikel tonen; dat staat dan ook klaar als de lezer kiest.
  document.querySelectorAll(".krant-kop").forEach(async (el) => {
    try {
      const r = await fetch("/api/voorpagina?krant=" + el.dataset.krant);
      const eerste = r.ok && (await r.json()).secties[0]?.artikelen[0];
      if (eerste) el.innerHTML = `<span class="krant-nu">Nu bovenaan</span>${veilig(eerste.titel)}`;
    } catch {}
  });
}

// ---------- Voorpagina van één krant ----------

async function toonStart(krant, rubriek) {
  $("start").hidden = false;
  $("krantknoppen").hidden = false;
  $("krant-titel").textContent = "Vandaag in " + KRANTEN[krant];
  document.title = KRANTEN[krant] + " — WimsKrant";
  try {
    const r = await fetch(`/api/voorpagina?krant=${krant}&rubriek=${encodeURIComponent(rubriek)}`);
    const data = await r.json();
    if (!r.ok) throw new Error(data.fout);
    // Snelmenu: voorpagina en rubrieken van deze krant; de huidige is ingekleurd.
    const knop = (slug, naam) => `<a href="/${krant}${slug ? "/" + slug : ""}"${
      slug === rubriek ? ' aria-current="page"' : ""}>${veilig(naam)}</a>`;
    $("rubrieken").innerHTML = knop("", "Voorpagina") + data.rubrieken.map((x) => knop(x.slug, x.naam)).join("");
    const naam = data.rubrieken.find((x) => x.slug === rubriek)?.naam;
    if (naam) {
      $("krant-titel").textContent = `${KRANTEN[krant]}: ${naam}`;
      document.title = `${naam} — ${KRANTEN[krant]} — WimsKrant`;
    }
    $("voorpagina").innerHTML = data.secties.map((s) => `
      <section class="sectie">
        <h2>${veilig(s.naam)}</h2>
        <div class="kaarten">${s.artikelen.map(kaart).join("")}</div>
      </section>`).join("");
  } catch (e) {
    $("voorpagina").innerHTML = `<p class="melding">De voorpagina van ${KRANTEN[krant]} kon niet worden geladen. ${veilig(e.message)}</p>`;
  }
}

function kaart(a) {
  return `<a class="kaart" href="${leesLink(a.url)}">
    ${a.beeld ? `<img src="${veilig(a.beeld)}" alt="" loading="lazy">` : `<span class="geen-beeld"></span>`}
    <span class="kaart-tekst">
      ${a.vlag ? `<span class="vlag">${veilig(a.vlag)}</span>` : ""}
      <strong>${veilig(a.titel)}</strong>
      ${a.teaser ? `<span class="teaser">${veilig(a.teaser)}</span>` : ""}
    </span></a>`;
}

// ---------- Woorden zoeken ----------

// Losse voornaamwoorden nooit als vaste schrijfwijze gebruiken: "hij" kan steeds iemand anders zijn.
// Tikt de lezer erop, dan zoekt /api/uitleg uit wie er bedoeld wordt.
const VOORNAAMWOORDEN = new Set("hij zij ze hem haar zijn hun hen die deze dat wie zich jij u".split(" "));

function maakZoeker(termen) {
  const vormen = [];
  termen.forEach((t, i) => (t.vormen?.length ? t.vormen : [t.term])
    .forEach((v) => v && !VOORNAAMWOORDEN.has(v.trim().toLowerCase()) && vormen.push({ v, i })));
  // Een schrijfwijze die bij meer dan één term hoort (bijv. een achternaam van vader én zoon) is
  // dubbelzinnig: niet automatisch onderstrepen. Tikken erop laat /api/uitleg uitzoeken wie het is.
  const terms = new Map();
  vormen.forEach(({ v, i }) => {
    const k = v.replace(/\s+/g, " ").toLowerCase();
    terms.set(k, (terms.get(k) || new Set()).add(i));
  });
  for (let n = vormen.length - 1; n >= 0; n--) {
    if (terms.get(vormen[n].v.replace(/\s+/g, " ").toLowerCase()).size > 1) vormen.splice(n, 1);
  }
  vormen.sort((a, b) => b.v.length - a.v.length); // langste eerst: "Community Food Bank" vóór "Food Bank"
  const perVorm = new Map(vormen.map((x) => [x.v.replace(/\s+/g, " ").toLowerCase(), x]));
  const deel = vormen.map((x) => x.v.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/\s+/g, "\\s+")).join("|");
  const re = new RegExp(`(?<![\\p{L}\\p{N}])(${deel})(?![\\p{L}\\p{N}])`, "giu");

  // Geeft de gevonden woorden in een stuk tekst terug, zonder overlap.
  return function zoek(tekst) {
    const gevonden = [];
    if (!vormen.length) return gevonden;
    for (const m of tekst.matchAll(re)) {
      const x = perVorm.get(m[0].replace(/\s+/g, " ").toLowerCase());
      if (!x) continue;
      // Afkortingen (SNAP, DES) alleen in hoofdletters, zodat bijv. "des" niet meetelt.
      if (x.v.length > 1 && x.v === x.v.toUpperCase() && m[0] !== x.v) continue;
      gevonden.push({ start: m.index, eind: m.index + m[0].length, i: x.i });
    }
    return gevonden;
  };
}

// ---------- Artikel opbouwen ----------

function datumTekst(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T12:00:00");
  return isNaN(d) ? iso : d.toLocaleDateString("nl-NL", { weekday: "long", day: "numeric", month: "long", year: "numeric" });
}

function bouwArtikel(art) {
  const termen = art.begrippen?.termen || [];
  const zoek = maakZoeker(termen);
  for (const k of [...NOTITIES.keys()]) if (!k.startsWith("v")) NOTITIES.delete(k); // zelf opgezochte woorden blijven
  const uitgelegd = new Set(); // woorden waarvan de uitleg al eerder klaarstond
  const getoond = new Set();   // voor het overzicht onderaan
  const delen = [];

  let gelezen = ""; // tekst tot en met het huidige blok, om te zien welke namen al gevallen zijn

  // Welke notitie hoort bij dit woord in blok nr? null = geen (de lezer kent het al).
  function kiesNoot(t, i, nr, zoals) {
    const eerder = (t.feiten || []).filter((f) => f.nr < nr);
    zoals = { tekst: zoals, naam: persoonsnaam(t), ook: (t.namen || []).filter((n) => gelezen.includes(n)) };
    if (t.uitleg && !uitgelegd.has(i)) {
      uitgelegd.add(i);
      return { html: notitieUitleg(t, i), eerste: true };
    }
    if (eerder.length) return { html: notitieHerinnering(t, i, zoals, eerder), eerste: false };
    if (t.uitleg) return { html: notitieKort(t, i, zoals), eerste: false };
    return null;
  }

  // Zet woorden in de tekst in een <mark>; de bijbehorende uitleg gaat naar NOTITIES (voor de popup).
  function verwerk(tekst, nr) {
    gelezen += "\n" + tekst;
    let html = "", pos = 0;
    const gehad = new Set();
    for (const g of zoek(tekst)) {
      if (gehad.has(g.i)) continue; // per alinea één keer
      gehad.add(g.i);
      const zoals = tekst.slice(g.start, g.eind);
      const n = kiesNoot(termen[g.i], g.i, nr, zoals);
      if (!n) continue;
      getoond.add(g.i);
      const sleutel = `${nr}-${g.i}`;
      NOTITIES.set(sleutel, n.html);
      const soort = termen[g.i].soort || "begrip";
      html += veilig(tekst.slice(pos, g.start));
      html += `<mark class="term soort-${soort}" data-noot="${sleutel}" data-i="${g.i}" role="button" tabindex="0">${veilig(zoals)}</mark>`;
      pos = g.eind;
    }
    html += veilig(tekst.slice(pos));
    return { html };
  }

  // Elk deel krijgt een vaste sleutel, zodat toon() alleen de delen vervangt die veranderd zijn.
  function rij(nr, binnen, sleutel = "r" + nr) {
    const n = nr == null ? "" : ` data-nr="${nr}"`;
    delen.push(`<section class="rij" data-sleutel="${veilig(sleutel)}"${n}><div class="tekst">${binnen}</div></section>`);
  }

  const meta = [art.bron.toUpperCase(), datumTekst(art.datum), art.auteurs?.length ? "door " + art.auteurs.join(", ") : ""]
    .filter(Boolean).join(" · ");
  const titel = verwerk(art.titel, 0);
  rij(0, `<p class="meta">${veilig(meta)}</p><h1 class="titel">${titel.html}</h1>`);

  const samenvatting = art.begrippen?.samenvatting || [];
  if (samenvatting.length) {
    delen.push(`<section class="rij" data-sleutel="samen"><div class="kort-samen"><h2>In het kort</h2><ul>${
      samenvatting.map((s) => `<li>${veilig(s)}</li>`).join("")}</ul></div></section>`);
  }
  if (art.bezig || art.uitlegFout) {
    delen.push(`<div class="voortgang-plek" data-sleutel="voortgang"></div>`);
  } else if (!art.begrippen && !art.kan_uitleggen) {
    delen.push(`<p class="melding" data-sleutel="geen">Voor dit artikel is nog geen uitleg gemaakt. Zet een ANTHROPIC_API_KEY in het bestand .env om dat automatisch te laten doen.</p>`);
  }
  if (art.kan_uitleggen) {
    delen.push(`<p class="tip" data-sleutel="tip">Tik op een <span class="voorbeeld">onderstreept woord</span> voor uitleg. U kunt ook op elk ander woord tikken.</p>`);
  }

  if (art.intro) {
    const intro = verwerk(art.intro, 1);
    rij(1, `<p class="intro">${intro.html}</p>`);
  }
  if (art.beeld && !art.blokken.some((b) => b.soort === "foto")) {
    rij(null, `<figure class="beeld"><img src="${veilig(art.beeld)}" alt=""></figure>`, "beeld");
  }

  for (const b of art.blokken) {
    if (b.soort === "foto") {
      const onder = b.bijschrift ? `<figcaption>${veilig(b.bijschrift)}</figcaption>` : "";
      rij(null, `<figure class="beeld"><img src="${veilig(b.src)}" alt="${veilig(b.bijschrift)}" loading="lazy">${onder}</figure>`, "foto-" + b.src);
    } else {
      const a = verwerk(b.tekst, b.nr);
      rij(b.nr, b.soort === "kop" ? `<h2>${a.html}</h2>` : `<p>${a.html}</p>`);
    }
  }

  // Overzicht van alle uitleg, per soort.
  const groepen = {};
  [...getoond].forEach((i) => (groepen[termen[i].soort || "begrip"] ||= []).push(termen[i]));
  const overzicht = Object.keys(KOPPEN).filter((s) => groepen[s]).map((s) =>
    `<h3>${KOPPEN[s]}</h3><dl>${groepen[s].sort((a, b) => a.term.localeCompare(b.term, "nl"))
      .map((t) => `<dt>${veilig(t.term)}</dt><dd>${veilig(samengevat(t))}</dd>`).join("")}</dl>`).join("");
  if (overzicht) delen.push(`<section class="overzicht" data-sleutel="overzicht"><h2>Alle uitleg op een rij</h2>${overzicht}</section>`);

  delen.push(`<p class="bron" data-sleutel="bron">Bron: <a href="${veilig(art.url)}" target="_blank" rel="noopener">het originele artikel</a>. Uitleg bij de woorden: WimsKrant.</p>`);
  return delen.join("");
}

function samengevat(t) {
  return [t.uitleg, t.rol && !t.uitleg ? t.rol + "." : "", ...(t.feiten || []).map((f) => f.feit)].filter(Boolean).join(" ");
}

function notitieUitleg(t, i) {
  const soort = t.soort || "begrip";
  return `<div class="noot nieuw soort-${soort}">
    <span class="soort">${SOORTNAAM[soort] || "Woord"}</span>
    <strong>${veilig(t.term)}</strong><p>${veilig(t.uitleg)}</p></div>`;
}

// Bij personen staat bovenaan altijd de naam, ook als in de tekst "zijn vader" of alleen de achternaam staat.
function persoonsnaam(t) {
  return t.soort === "persoon" ? t.term : null;
}

// Kop van een notitie: de naam (bij personen) of het woord zoals het in de tekst staat,
// plus de andere namen waaronder het artikel deze persoon tot nu toe noemde.
function kop(t, zoals) {
  const naam = zoals.naam || zoals.tekst;
  const anders = zoals.naam && !zoals.naam.toLowerCase().includes(zoals.tekst.toLowerCase())
    ? ` <span class="in-tekst">(in de tekst: „${veilig(zoals.tekst)}”)</span>` : "";
  const ook = [...new Set(zoals.ook)].filter((n) => !naam.toLowerCase().includes(n.toLowerCase()));
  return `<strong>${veilig(naam)}</strong>${anders}` +
    (ook.length ? `<span class="ook">Ook genoemd: ${ook.map(veilig).join(", ")}</span>` : "");
}

function notitieHerinnering(t, i, zoals, eerder) {
  const soort = t.soort || "begrip";
  // "de koning" + rol "de koning, bezoekt een azc" → alleen "bezoekt een azc"
  let r = t.rol || "";
  const naam = zoals.naam || zoals.tekst;
  if (r.toLowerCase().startsWith(naam.toLowerCase())) r = r.slice(naam.length).replace(/^[\s,:–-]+/, "");
  const rol = r ? `<span class="rol">${veilig(r)}</span>` : "";
  const feiten = eerder.slice(-3).map((f) => `<li>${veilig(f.feit)}</li>`).join("");
  return `<div class="noot herhaal soort-${soort}">
    <span class="soort">Weet u nog?</span>${kop(t, zoals)}${rol ? " " + rol : ""}
    <ul>${feiten}</ul></div>`;
}

function notitieKort(t, i, zoals) {
  return `<div class="noot herhaal soort-${t.soort || "begrip"}">
    <span class="soort">Weet u nog?</span>${kop(t, zoals)}: ${veilig(t.kort || t.uitleg)}</div>`;
}

// ---------- Popup met uitleg ----------

const NOTITIES = new Map(); // "nr-term" of "v1" -> HTML van de uitleg
let openMark = null;

function openPopup(html, mark) {
  sluitPopup();
  $("popup-inhoud").innerHTML = html;
  $("popup").hidden = false;
  openMark = mark || null;
  openMark?.classList.add("open");
  $("popup-sluit").focus({ preventScroll: true });
}

function sluitPopup() {
  $("popup").hidden = true;
  openMark?.classList.remove("open");
  openMark?.focus({ preventScroll: true });
  openMark = null;
}

function koppel(artikel, art) {
  $("popup-sluit").onclick = sluitPopup;
  $("popup").addEventListener("click", (ev) => { if (ev.target === $("popup")) sluitPopup(); });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !$("popup").hidden) sluitPopup();
    const mark = ev.target.closest?.("mark.term");
    if (mark && (ev.key === "Enter" || ev.key === " ")) { ev.preventDefault(); mark.click(); }
  });
  artikel.addEventListener("click", (ev) => {
    const mark = ev.target.closest("mark.term");
    if (mark) {
      const html = NOTITIES.get(mark.dataset.noot);
      if (html) openPopup(html, mark);
      return;
    }
    const tekst = ev.target.closest(".rij[data-nr] .tekst");
    if (tekst && art.kan_uitleggen && !ev.target.closest(".meta")) zoekOp(ev, tekst.closest(".rij"), art);
  });
}

// ---------- Zelf een woord opzoeken ----------

let vraagTeller = 0;

function woordBijKlik(ev) {
  const sel = getSelection();
  if (sel && !sel.isCollapsed) {
    const t = sel.toString().replace(/\s+/g, " ").trim();
    if (t.length > 1 && t.length <= 80) return { woord: t, bereik: sel.getRangeAt(0) };
  }
  let r = null;
  if (document.caretRangeFromPoint) r = document.caretRangeFromPoint(ev.clientX, ev.clientY);
  else if (document.caretPositionFromPoint) {
    const p = document.caretPositionFromPoint(ev.clientX, ev.clientY);
    if (p) { r = document.createRange(); r.setStart(p.offsetNode, p.offset); }
  }
  if (!r || r.startContainer.nodeType !== Node.TEXT_NODE) return null;
  const s = r.startContainer.data, letter = /[\p{L}\p{N}'’-]/u;
  let a = r.startOffset, b = a;
  while (a > 0 && letter.test(s[a - 1])) a--;
  while (b < s.length && letter.test(s[b])) b++;
  while (a < b && /[-'’]/.test(s[a])) a++;
  while (b > a && /[-'’]/.test(s[b - 1])) b--;
  if (b - a < 2) return null;
  const bereik = document.createRange();
  bereik.setStart(r.startContainer, a);
  bereik.setEnd(r.startContainer, b);
  return { woord: s.slice(a, b), bereik };
}

function vraagNotitie(woord, uitleg) {
  return `<div class="noot gevraagd"><span class="soort">U vroeg</span><strong>${veilig(woord)}</strong>
    <p>${uitleg ? veilig(uitleg) : '<span class="laden">Even opzoeken…</span>'}</p></div>`;
}

async function zoekOp(ev, rij, art) {
  const gevonden = woordBijKlik(ev);
  if (!gevonden) return;
  const nr = +rij.dataset.nr;
  const id = "v" + ++vraagTeller;
  let mark = null;
  try {
    mark = document.createElement("mark");
    mark.className = "term opgezocht";
    mark.dataset.noot = id;
    mark.setAttribute("role", "button");
    mark.tabIndex = 0;
    gevonden.bereik.surroundContents(mark);
  } catch { mark = null; /* selectie over meerdere opmaakdelen: dan zonder markering */ }
  getSelection()?.removeAllRanges();

  NOTITIES.set(id, vraagNotitie(gevonden.woord));
  openPopup(NOTITIES.get(id), mark);
  let uitleg;
  try {
    const r = await fetch(`/api/uitleg?url=${encodeURIComponent(art.url)}&nr=${nr}&woord=${encodeURIComponent(gevonden.woord)}`);
    const d = await r.json();
    if (!r.ok) throw new Error(d.fout);
    uitleg = d.uitleg;
    NOTITIES.set(id, vraagNotitie(gevonden.woord, uitleg));
  } catch (e) {
    uitleg = e.message || "Er kon nu geen uitleg worden gevonden.";
    NOTITIES.delete(id);
  }
  // Alleen bijwerken als deze popup nog openstaat.
  if (!$("popup").hidden && openMark === mark) $("popup-inhoud").querySelector("p").textContent = uitleg;
}

// ---------- Lettergrootte ----------

function letters() {
  let grootte = lees("wk-letters", 1.375);
  const zet = () => { document.documentElement.style.setProperty("--tekst", grootte + "rem"); bewaar("wk-letters", grootte); };
  if (lees("wk-letters", null)) zet();
  $("groter").onclick = () => { grootte = Math.min(2.2, +(grootte + 0.125).toFixed(3)); zet(); };
  $("kleiner").onclick = () => { grootte = Math.max(1, +(grootte - 0.125).toFixed(3)); zet(); };
}

// ---------- Menubalk: weg bij omlaag scrollen, terug bij omhoog of bovenaan ----------

function menubalk() {
  const balk = document.querySelector(".balk");
  let vorige = scrollY;
  addEventListener("scroll", () => {
    const y = scrollY;
    if (y < 80) balk.classList.remove("weg");
    else if (y > vorige + 10) balk.classList.add("weg");
    else if (y < vorige - 10) balk.classList.remove("weg");
    else return; // kleine bewegingen negeren, zodat de balk niet trilt
    vorige = y;
  }, { passive: true });
}

// ---------- Lezen ----------

function toon(art) {
  const houder = $("artikel");
  // Plek op de pagina vasthouden als er uitleg bijkomt.
  const anker = [...houder.querySelectorAll(".rij[data-nr]")].find((r) => r.getBoundingClientRect().bottom > 80);
  const verschil = anker ? anker.getBoundingClientRect().top : 0;

  // Alleen de delen vervangen die veranderd zijn. Delen met een zelf opgezocht woord of een
  // open popup blijven staan, zodat de lezer niets kwijtraakt.
  const nieuw = document.createElement("div");
  nieuw.innerHTML = bouwArtikel(art);
  const oud = new Map([...houder.children].map((el) => [el.dataset.sleutel, el]));
  houder.replaceChildren(...[...nieuw.children].map((el) => {
    const was = el.dataset.sleutel && oud.get(el.dataset.sleutel);
    if (!was) return el;
    if (el.dataset.sleutel === "voortgang" || was.outerHTML === el.outerHTML || was.querySelector(".opgezocht, .open")) return was;
    return el;
  }));

  const plek = houder.querySelector(".voortgang-plek");
  if (plek && !plek.contains(VOORTGANG)) plek.append(VOORTGANG);
  toonVoortgang(art);

  // Woorden die er net bij gekomen zijn, lichten even op.
  const marks = [...houder.querySelectorAll("mark.term[data-i]")];
  if (art.bekend) marks.forEach((m) => { if (!art.bekend.has(m.dataset.i)) m.classList.add("nieuw"); });
  art.bekend = new Set(marks.map((m) => m.dataset.i));

  if (anker && anker.isConnected) window.scrollBy(0, anker.getBoundingClientRect().top - verschil);
  else if (anker) {
    const zelfde = houder.querySelector(`.rij[data-nr="${anker.dataset.nr}"]`);
    if (zelfde) window.scrollBy(0, zelfde.getBoundingClientRect().top - verschil);
  }
}

// ---------- Voortgang van de uitleg ----------

const VOORTGANG = document.createElement("div");
VOORTGANG.className = "voortgang";
VOORTGANG.setAttribute("role", "status");
VOORTGANG.innerHTML = `<p class="voortgang-tekst"></p><div class="voortgang-balk"><div class="voortgang-vulling"></div></div>`;

function toonVoortgang(art) {
  const tekst = VOORTGANG.querySelector(".voortgang-tekst");
  const balk = VOORTGANG.querySelector(".voortgang-balk");
  if (art.uitlegFout) {
    tekst.textContent = art.uitlegFout;
    balk.hidden = true;
    return;
  }
  if (!art.bezig) return;
  const termen = art.begrippen?.termen || [];
  const n = termen.length;
  tekst.textContent = n
    ? `${n} ${n === 1 ? "woord" : "woorden"} uitgelegd. Er komen er nog meer bij; u kunt gewoon doorlezen.`
    : art.begrippen?.samenvatting?.length
      ? "De samenvatting staat klaar. Nu volgen de woorden in het artikel…"
      : "De uitleg bij dit artikel wordt gemaakt. U kunt alvast lezen; de onderstreepte woorden komen er vanzelf bij.";
  // Schatting: hoeveel woorden er (gemeten) ongeveer komen, ca. 8 + 1 per 70 woorden tekst, hooguit 40;
  // de klok houdt de balk in beweging zolang er nog niets binnen is (ca. 1,5 s per woord).
  const lengte = art.blokken.reduce((s, b) => s + (b.tekst || "").split(/\s+/).length, 0);
  const verwacht = Math.min(40, 8 + lengte / 70);
  const sec = (Date.now() - art.begin) / 1000;
  const klok = 0.9 * (1 - Math.exp(-sec / (verwacht * 1.6)));
  const deel = Math.min(0.95, Math.max(art.deel || 0.04, klok, (n / verwacht) * 0.95));
  art.deel = deel;
  VOORTGANG.querySelector(".voortgang-vulling").style.width = Math.round(deel * 100) + "%";
}

async function laadUitleg(art) {
  art.bezig = true;
  art.begin = Date.now();
  art.begrippen = { samenvatting: [], termen: [] };
  toon(art);
  const klok = setInterval(() => toonVoortgang(art), 1000);
  let gepland = null;
  const straks = () => { gepland ||= setTimeout(() => { gepland = null; toon(art); }, 1200); };
  try {
    const r = await fetch("/api/begrippen?url=" + encodeURIComponent(art.url));
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).fout || "");
    const lezer = r.body.getReader();
    const ontcijfer = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await lezer.read();
      if (done) break;
      buffer += ontcijfer.decode(value, { stream: true });
      let k;
      while ((k = buffer.indexOf("\n\n")) >= 0) {
        const blok = buffer.slice(0, k);
        buffer = buffer.slice(k + 2);
        const soort = /^event: (.*)$/m.exec(blok)?.[1];
        const data = /^data: (.*)$/m.exec(blok)?.[1];
        if (!soort || !data) continue;
        const d = JSON.parse(data);
        if (soort === "samenvatting") { art.begrippen.samenvatting = d; straks(); }
        else if (soort === "term") { art.begrippen.termen.push(d); straks(); }
        else if (soort === "klaar") { art.begrippen = d; art.bezig = false; }
        else if (soort === "fout") throw new Error(d.fout);
      }
    }
    if (art.bezig) throw new Error("De verbinding viel weg.");
  } catch (e) {
    art.bezig = false;
    art.uitlegFout = "Het maken van de uitleg is niet (helemaal) gelukt. " + (e.message || "") +
      (art.begrippen.termen.length ? " De woorden die al klaar waren, zijn wel onderstreept." : "");
  }
  clearTimeout(gepland);
  clearInterval(klok);
  toon(art);
}

async function toonArtikel(url) {
  $("lezen").hidden = false;
  let art;
  try {
    const r = await fetch("/api/artikel?url=" + encodeURIComponent(url));
    art = await r.json();
    if (!r.ok) throw new Error(art.fout || "Er ging iets mis.");
  } catch (e) {
    $("bezig").hidden = true;
    $("artikel").innerHTML = `<div class="fout"><p><strong>Dit artikel kan niet worden getoond.</strong></p><p>${veilig(e.message)}</p><p><a href="/">Terug naar de voorpagina</a></p></div>`;
    return;
  }
  document.title = art.titel + " — WimsKrant";
  $("bezig").hidden = true;
  $("leesknoppen").hidden = false;
  // Terug naar de pagina waar het artikel gekozen werd (voorpagina of rubriek).
  const via = document.referrer && new URL(document.referrer);
  if (via && via.origin === location.origin && KRANTEN[via.pathname.split("/")[1]]) $("naar-voorpagina").href = via.pathname;
  else if (KRANTEN[art.krant]) $("naar-voorpagina").href = "/" + art.krant;
  // Knop naar hetzelfde artikel op de website van de krant zelf.
  $("naar-krant").href = art.url;
  $("naar-krant").textContent = `${KRANTEN[art.krant] ? "In " + KRANTEN[art.krant] : "Origineel"} ↗`;
  $("naar-krant").hidden = false;
  toon(art);
  koppel($("artikel"), art);
  letters();
  const eerder = lees("wk-eerder", []).filter((e) => e.url !== url);
  bewaar("wk-eerder", [{ url, titel: art.titel, krant: art.krant, op: Date.now() }, ...eerder].slice(0, 300));

  if (!art.begrippen && art.kan_uitleggen) laadUitleg(art);
}

menubalk();
const url = new URLSearchParams(location.search).get("url");
const [krant, rubriek = ""] = location.pathname.split("/").filter(Boolean);
if (location.pathname === "/lees" && url) toonArtikel(url);
else if (KRANTEN[krant]) toonStart(krant, rubriek);
else toonKeuze();
