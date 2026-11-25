"use strict";
const $ = (id) => document.getElementById(id);
const fmt = (n, d = 2) => Number(n).toLocaleString("en-GB",
  { minimumFractionDigits:d, maximumFractionDigits:d });

async function cmd(line) {
  if (!line.trim()) return;
  const r = await fetch("/cmd", { method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({ line }) });
  const data = await r.json();
  log(humanise(data.out, line));
  poll();
}

function humanise(text, line) {
  const t = (text || "").replace(/^\s+|\s+$/g, "");
  return (line ? "> " + line + "\n" : "") + t;
}

function log(text) {
  const el = $("news");
  const div = document.createElement("div");
  div.className = "e" + (/\bERR:/.test(text) ? " er" : /\bOK:/.test(text) ? " ok" : "");
  div.textContent = text;
  el.prepend(div);
  while (el.children.length > 40) el.lastChild.remove();
}

function row(k, v, cls) { return `<div class="row"><span class="dim">${k}</span><span class="${cls||""}">${v}</span></div>`; }

function draw(s) {
  $("site").textContent = s.site.toLowerCase()
    + (s.machine ? "  |  " + s.machine.toLowerCase() + " machine, cycle " + s.cycle : "");
  $("tick").textContent = "tick " + s.tick;
  $("cash").textContent = fmt(s.cash);
  $("net").textContent = (s.net >= 0 ? "+" : "") + fmt(s.net, 3) + "/tick";
  $("net").className = s.net >= 0 ? "dim" : "bad";
  $("won").classList.toggle("hidden", !s.won);

  $("cap").textContent = s.capacity;
  $("capwhy").textContent = s.working_lines < s.working_cores * 8 ? "limited by lines"
                          : s.working_cores * 8 < s.working_lines ? "limited by modules"
                          : "lines and modules matched";
  $("callers").textContent = s.served + " / " + s.subs;
  $("queue").textContent = s.queued ? s.queued + " queued behind " + s.slots + " slots"
                                     : "no queue, " + s.slots + " slots";
  $("rev").textContent = fmt(s.revenue, 3);
  $("outg").textContent = "-" + fmt(s.outgoings, 3) + " costs";
  $("rep").textContent = fmt(s.rep, 2);
  $("trend").textContent = (s.rep_trend >= 0 ? "+" : "") + fmt(s.rep_trend, 3) + "/tick, "
    + Math.round(s.refused_share * 100) + "% refused";

  $("goalv").textContent = s.won ? "yours" : fmt(s.cash, 0) + " / " + fmt(s.goal.price, 0);
  $("goalfill").style.width = s.goal.pct + "%";
  $("goalsub").textContent = (s.won ? "bought on tick " + s.won_tick
    : s.goal.pct.toFixed(1) + "% towards the deeds")
    + (s.standing ? "   |   standing " + s.standing
       + " (+" + (4 * s.standing) + "% revenue, +" + (3 * s.standing) + "% demand)" : "");

  $("staff").innerHTML = s.staff.length
    ? s.staff.map(x => row(x.role.toLowerCase() + " " + x.name, fmt(x.salary, 2) + "/tick")).join("")
    : '<div class="empty">nobody works here but you</div>';

  $("mods").innerHTML = s.mods.length
    ? s.mods.map(m => row(m.name.toLowerCase(), m.left + " ticks", m.good ? "good" : "bad")).join("")
    : '<div class="empty">ordinary weather</div>';

  $("faults").innerHTML = s.faults.length
    ? s.faults.slice(0, 8).map(f => row(f.kind + " " + f.n, fmt(f.cost, 0) + " to fix", "bad")).join("")
      + (s.faults.length > 8 ? `<div class="empty">and ${s.faults.length - 8} more</div>` : "")
    : '<div class="empty">nothing is down</div>';

  $("offers").innerHTML = s.offers.length
    ? s.offers.map(o => `<div class="row"><span>${o.state === "active" ? "LIVE " : ""}${o.company.toLowerCase()}</span>
        <span class="amber">${o.want} callers</span></div>
        <div class="row small dim"><span>${o.state === "active" ? o.breached + " short, " + o.left + " left" : o.duration + " ticks"}</span>
        <span>fee ${fmt(o.fee,0)} / penalty ${fmt(o.penalty,0)}</span></div>`).join("")
    : '<div class="empty">nothing on the teleprinter</div>';

  $("ups").innerHTML = s.upgrades.length
    ? s.upgrades.map(u => `<span class="chip">${u.toLowerCase()}</span>`).join("")
    : '<span class="empty">no upgrades fitted</span>';

  if (s.news && s.news.length) {
    const el = $("news");
    if (!el.dataset.seeded) { s.news.forEach(n => log(n)); el.dataset.seeded = "1"; }
  }
  chart(s);
}

function chart(s) {
  const cv = $("chart");
  const dpr = window.devicePixelRatio || 1;
  const w = cv.clientWidth, h = 120;
  cv.width = w * dpr; cv.height = h * dpr;
  const g = cv.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const pts = (s.history || []);
  g.strokeStyle = "#243044"; g.lineWidth = 1;
  for (let i = 1; i < 4; i++) { const y = (h / 4) * i; g.beginPath(); g.moveTo(0, y); g.lineTo(w, y); g.stroke(); }
  if (pts.length < 2) { g.fillStyle = "#6b7a8d"; g.fillText("waiting for ticks", 8, 20); return; }
  const cashes = pts.map(p => p[1]);
  const lo = Math.min(...cashes), hi = Math.max(...cashes);
  const span = (hi - lo) || 1;
  const X = i => (i / (pts.length - 1)) * (w - 2) + 1;
  const Y = v => h - 6 - ((v - lo) / span) * (h - 18);
  g.beginPath();
  pts.forEach((p, i) => i ? g.lineTo(X(i), Y(p[1])) : g.moveTo(X(i), Y(p[1])));
  g.strokeStyle = "#ffc857"; g.lineWidth = 2; g.stroke();
  g.lineTo(X(pts.length - 1), h); g.lineTo(X(0), h); g.closePath();
  g.fillStyle = "rgba(255,200,87,0.10)"; g.fill();
  g.fillStyle = "#6b7a8d"; g.font = "10px monospace";
  g.fillText(fmt(hi, 0), 4, 12); g.fillText(fmt(lo, 0), 4, h - 4);
}

async function poll() {
  try {
    const s = await (await fetch("/state")).json();
    draw(s);
  } catch (e) {
    $("tick").textContent = "offline";
  }
}

$("cmd").addEventListener("keydown", (e) => {
  if (e.key === "Enter") { const v = e.target.value; e.target.value = ""; cmd(v); }
});
$("cmd").focus();
poll();
setInterval(poll, 1000);
