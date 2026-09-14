/* ============================================================
   IC Events Approval Hub — single-page application
   ============================================================ */
"use strict";

const S = {
  uploadLimit: 0,        // the largest file this deployment can actually accept
  canMoveDates: false,   // set from the session: only IC and admins may
  dashYear: "all", dashMonth: "all",   // the period the dashboard figures cover
  recordKind: "all",     // which kind of record the history is showing
  user: null,
  settings: { currency: "SAR", default_vat_rate: 15 },
  lookups: { event_types: [], vendor_categories: [], approvers: [] },
  unread: 0,
  scoped: false,          // true when signed in through a passwordless review link
  scopeEventId: null,
  route: { name: "dashboard", id: null },
  filters: { search: "", status: "all", vendor_status: "all", event_type: "all", approver: "", date_from: "", date_to: "" },
  eventsTab: "active",
  archiveView: "calendar",   // completed events default to the month calendar
  calMonth: null,
  archive: [],
  archiveReadOnly: false,
  decision: null,
  adminTab: "users",
  menuOpen: false,
};

/* ------------------------------------------------------------- utilities */
const $ = (sel, ctx) => (ctx || document).querySelector(sel);
const $$ = (sel, ctx) => Array.from((ctx || document).querySelectorAll(sel));
const val = (sel) => { const el = $(sel); return el ? el.value.trim() : ""; };
const numVal = (sel) => { const el = $(sel); return el ? parseFloat(el.value || 0) || 0 : 0; };

function esc(v) {
  if (v === null || v === undefined) return "";
  return String(v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function money(v, withCur = true) {
  const n = Number(v || 0);
  const s = n.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return withCur ? `${s} ${S.settings.currency || "SAR"}` : s;
}
function fmtDate(d) {
  if (!d) return "—";
  const dt = new Date(String(d).replace(" ", "T"));
  if (isNaN(dt)) return String(d);
  return dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" });
}
function fmtDateTime(d) {
  if (!d) return "—";
  const dt = new Date(String(d).replace(" ", "T"));
  if (isNaN(dt)) return String(d);
  return dt.toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "numeric" }) +
    " · " + dt.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
function fmtTime(t) { return t ? String(t) : "—"; }
function fmtSize(b) {
  if (!b && b !== 0) return "";
  if (b < 1024) return b + " B";
  if (b < 1048576) return (b / 1024).toFixed(0) + " KB";
  return (b / 1048576).toFixed(1) + " MB";
}
function initials(name) {
  return String(name || "?").split(/\s+/).slice(0, 2).map(w => w[0]).join("").toUpperCase();
}
function ordinal(n) {
  const s = ["th", "st", "nd", "rd"], v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
function roleLabel(u) {
  if (u.role === "admin") return "Administrator (IC)";
  if (u.role === "manager")
    return `${ordinal(u.approver_level || 1)} approver${u.is_final_approver ? " · final" : ""}`;
  return "IC member";
}

const STATUS = {
  draft: { label: "Draft", cls: "b-draft", icon: "note" },
  pending_approval: { label: "Pending Approval", cls: "b-pending", icon: "hourglass" },
  pending_vendor_approval: { label: "Pending Vendor Approval", cls: "b-pending", icon: "hourglass" },
  partially_approved: { label: "Partially Approved", cls: "b-partial", icon: "checks" },
  fully_approved: { label: "Fully Approved", cls: "b-approved", icon: "circleCheck" },
  rejected: { label: "Rejected", cls: "b-rejected", icon: "circleX" },
  cancelled: { label: "Cancelled", cls: "b-cancelled", icon: "ban" },
  completed: { label: "Completed", cls: "b-completed", icon: "flag" },
};
function statusBadge(st) {
  const m = STATUS[st] || { label: st || "—", cls: "b-neutral", icon: "info" };
  return `<span class="badge ${m.cls}">${icon(m.icon)}${esc(m.label)}</span>`;
}
const VSTATUS = {
  pending: { label: "Pending", cls: "b-pending", icon: "clock" },
  approved: { label: "Approved", cls: "b-approved", icon: "circleCheck" },
  rejected: { label: "Rejected", cls: "b-rejected", icon: "circleX" },
  not_selected: { label: "Not selected", cls: "b-cancelled", icon: "ban" },   // left out by the approver
};
function vendorBadge(st) {
  const m = VSTATUS[st] || VSTATUS.pending;
  return `<span class="badge ${m.cls}">${icon(m.icon)}${esc(m.label)}</span>`;
}

/* "Approved by Mohammed Al-Otaibi · 16 Aug 2026" — who put the decision in. */
function decidedBy(name, when, status) {
  if (!name) return "";
  const verb = status === "rejected" ? "Rejected" : status === "not_selected" ? "Set aside" : "Approved";
  return `<div class="by-line ${status === "rejected" ? "bad" : "good"}">
    ${icon("user", "icon-sm")}<span>${esc(verb)} by <b>${esc(name)}</b>${when ? ` · ${fmtDate(when)}` : ""}</span></div>`;
}
function vendorSummaryBadge(sum) {
  if (!sum || !sum.total) return `<span class="badge b-neutral">No vendors</span>`;
  // `undecided` means the request is still moving through the chain. An earlier
  // approver's ticks are real, but they are not the event's answer: a later approver
  // can choose a different package and every one of those vendors becomes not-selected.
  // This badge used to work the counts out for itself and so read "1/1 Approved" beside
  // a status of Pending Approval -- the server says which it is, and this trusts it.
  // Until the final approval it is simply pending. A count here invited the reader to
  // work out how far along it was, which is the question the badge cannot answer:
  // a later approver can still choose a different package altogether.
  if (sum.undecided || sum.pending === sum.total) {
    return `<span class="badge b-pending">${icon("clock")}Pending</span>`;
  }
  const cls = sum.rejected > 0 ? (sum.approved > 0 ? "b-partial" : "b-rejected") : "b-approved";
  return `<span class="badge ${cls}">${icon("checks")}${sum.approved}/${sum.total} Approved</span>`;
}

const MONTH_NAMES = ["January", "February", "March", "April", "May", "June",
                     "July", "August", "September", "October", "November", "December"];

const RECORD_KINDS = [
  { k: "all", t: "Everything", i: "checks" },
  { k: "activity", t: "Events & activities", i: "calendar" },
  { k: "monthly_recap", t: "Monthly recaps", i: "note" },
  { k: "quarterly_update", t: "Quarterly updates", i: "clipboard" },
];

const KIND_LABEL = {
  activity: "Event or activity",
  monthly_recap: "Monthly recap",
  quarterly_update: "Quarterly update",
};

function kindOf(e) {
  return e.record_kind || "activity";
}

function kindBadge(e) {
  const k = kindOf(e);
  if (k === "activity") return "";
  const cls = k === "monthly_recap" ? "b-partial" : "b-completed";
  return `<span class="badge ${cls}">${icon(k === "monthly_recap" ? "note" : "clipboard")}${esc(KIND_LABEL[k])}</span>`;
}

/* ------------------------------------------------------------------- api */
async function api(path, opts = {}) {
  const method = opts.method || "GET";
  // State the route in a header too: hosting platforms rewrite request paths,
  // and this is immune to that.
  const headers = { "X-Requested-With": "ic-hub", "X-IC-Path": path };
  if (opts.body) headers["Content-Type"] = "application/json";
  const res = await fetch(path, {
    method, headers, credentials: "same-origin",
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  let data = {};
  const ct = res.headers.get("Content-Type") || "";
  if (ct.includes("application/json")) { try { data = await res.json(); } catch (e) { data = {}; } }
  if (!res.ok) {
    if (res.status === 401 && S.user) { S.user = null; location.hash = ""; render(); }
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.status = res.status; err.data = data;
    throw err;
  }
  return data;
}

/* Fetch a file and hand it to the browser as a download.

   A plain <a href="/api/..."> is a navigation, so it carries none of our headers --
   and a hosting platform that rewrites request paths then has nothing to go on. Doing
   it through fetch() keeps the route in X-IC-Path, exactly like every other call. */
async function download(path, filename) {
  const res = await fetch(path, {
    headers: { "X-Requested-With": "ic-hub", "X-IC-Path": path },
    credentials: "same-origin",
  });
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try { const j = await res.json(); if (j.error) msg = j.error; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  const blob = await res.blob();
  const stated = res.headers.get("Content-Disposition") || "";
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(stated);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename || (match ? decodeURIComponent(match[1]) : "download");
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 20000);
}

/* Pictures come from the API, and a plain <img src="/api/..."> is a bare navigation:
   it carries none of our headers, so a host that rewrites request paths has nothing to
   route on and the picture arrives as a broken icon. Markup therefore names the route in
   data-api-src, and this fetches each one the same way every other call is made, handing
   the bytes to the <img> as a blob. Results are cached, so a picture is fetched once per
   session however many times it is rendered. */
const BLOBS = new Map();

async function apiImageUrl(path) {
  if (BLOBS.has(path)) return BLOBS.get(path);
  const res = await fetch(path, {
    headers: { "X-Requested-With": "ic-hub", "X-IC-Path": path },
    credentials: "same-origin",
  });
  if (!res.ok) throw new Error(`image ${res.status}`);
  const url = URL.createObjectURL(await res.blob());
  BLOBS.set(path, url);
  return url;
}

async function hydrateImages(root = document) {
  const pending = [...root.querySelectorAll("img[data-api-src]:not([data-loaded])")];
  await Promise.all(pending.map(async (img) => {
    const path = img.dataset.apiSrc;
    img.dataset.loaded = "1";
    try {
      img.src = await apiImageUrl(path);
    } catch (err) {
      img.dataset.loaded = "failed";
      img.replaceWith(Object.assign(document.createElement("div"), {
        className: "img-missing",
        title: "This picture could not be loaded",
        innerHTML: icon("photo", "icon-lg"),
      }));
    }
  }));
  // anchors that open a file get the same treatment, so "View" works too
  for (const a of root.querySelectorAll("a[data-api-href]:not([data-loaded])")) {
    a.dataset.loaded = "1";
    a.addEventListener("click", async (ev) => {
      ev.preventDefault();
      try { window.open(await apiImageUrl(a.dataset.apiHref), "_blank", "noopener"); }
      catch (err) { toast("Could not open the file", err.message, "bad"); }
    });
  }
}

/* ---------------------------------------------------------------- toasts */
function toast(title, message, kind = "ok") {
  const box = $("#toasts");
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  const ic = kind === "ok" ? "circleCheck" : kind === "err" ? "alert" : "info";
  el.innerHTML = `${icon(ic, "icon-lg")}<div><b>${esc(title)}</b>${message ? `<p>${esc(message)}</p>` : ""}</div>`;
  box.appendChild(el);
  setTimeout(() => { el.style.opacity = "0"; el.style.transform = "translateX(22px)"; el.style.transition = ".25s"; }, kind === "err" ? 6000 : 3800);
  setTimeout(() => el.remove(), kind === "err" ? 6400 : 4200);
}
function apiError(e) { toast("Something went wrong", e.message || "Unexpected error", "err"); }

/* ---------------------------------------------------------------- modals */
function closeModal() { $("#modal-root").innerHTML = ""; }
function openModal(html) {
  $("#modal-root").innerHTML = html;
  hydrateImages($("#modal-root"));
}

/* A modal that holds a form. `onConfirm` returns false to keep it open (so validation
   can complain), anything else closes it. Errors surface as a toast and leave the
   entered values in place rather than throwing the work away. */
function formModal(bodyHtml, confirmLabel, onConfirm, wide = true) {
  openModal(`
    <div class="overlay" id="fm-ov">
      <div class="modal${wide ? " wide" : " narrow"}">
        <div class="modal-head"><div id="fm-title"></div>
          <button class="x-btn" id="fm-x">${icon("x")}</button></div>
        <div class="modal-body" id="fm-body">${bodyHtml}</div>
        <div class="modal-foot">
          <button class="btn" id="fm-no">Cancel</button>
          <button class="btn btn-primary" id="fm-yes">${icon("check")}${esc(confirmLabel)}</button>
        </div>
      </div>
    </div>`);
  // the heading travels with the body markup; lift it into the head bar
  const heading = $("#fm-body").querySelector("h3");
  if (heading) { $("#fm-title").appendChild(heading); }
  $("#fm-x").onclick = closeModal;
  $("#fm-no").onclick = closeModal;
  $("#fm-ov").onclick = (e) => { if (e.target.id === "fm-ov") closeModal(); };
  $("#fm-yes").onclick = async () => {
    const btn = $("#fm-yes");
    btn.disabled = true;
    try {
      const keep = await onConfirm();
      if (keep !== false) closeModal();
    } catch (err) {
      toast("Could not save", err.message, "bad");
    } finally {
      if ($("#fm-yes")) btn.disabled = false;
    }
  };
}

function confirmDialog({ title, message, confirmLabel = "Confirm", tone = "primary", requireText = null, placeholder = "" }) {
  return new Promise(resolve => {
    openModal(`
      <div class="overlay" id="cf-ov">
        <div class="modal narrow">
          <div class="modal-head"><h3>${esc(title)}</h3>
            <button class="x-btn" id="cf-x">${icon("x")}</button></div>
          <div class="modal-body">
            <p class="muted" style="margin:0">${message}</p>
            ${requireText ? `<div class="field mt"><label>${esc(requireText)} <span class="req">*</span></label>
              <textarea id="cf-text" placeholder="${esc(placeholder)}"></textarea>
              <div class="hint" id="cf-err" style="color:var(--bad);display:none">This field is required.</div></div>` : ""}
          </div>
          <div class="modal-foot">
            <button class="btn" id="cf-no">Cancel</button>
            <button class="btn btn-${tone}" id="cf-yes">${esc(confirmLabel)}</button>
          </div>
        </div>
      </div>`);
    const done = (v) => { closeModal(); resolve(v); };
    $("#cf-x").onclick = () => done(null);
    $("#cf-no").onclick = () => done(null);
    $("#cf-ov").onclick = (e) => { if (e.target.id === "cf-ov") done(null); };
    $("#cf-yes").onclick = () => {
      if (requireText) {
        const t = $("#cf-text").value.trim();
        if (!t) { $("#cf-err").style.display = "block"; $("#cf-text").focus(); return; }
        return done(t);
      }
      done(true);
    };
    if (requireText) setTimeout(() => $("#cf-text").focus(), 60);
  });
}

function lightbox(url, group) {
  // The whole picture, fitted to the window. A photograph taken on a phone is far
  // larger than any screen, and the old viewer let it overflow -- so the middle filled
  // the screen and the edges were simply gone. Fitting first, zooming on request, is
  // the right way round: you look before you inspect.
  const shots = (group && group.length ? group : [url]);
  let at = Math.max(0, shots.indexOf(url));

  const draw = () => {
    const shot = shots[at];
    const many = shots.length > 1;
    openModal(`
      <div class="lightbox" id="lb">
        <div class="lb-bar">
          ${shot.kind ? `<span class="lb-kind">${esc(shot.kind)}</span>` : ""}
          <span class="lb-name">${esc(shot.caption || shot.name || "")}</span>
          ${many ? `<span class="lb-count">${at + 1} of ${shots.length}</span>` : ""}
          <button class="lb-btn" id="lb-zoom" title="Actual size">${icon("search")}</button>
          <button class="lb-btn" id="lb-x" title="Close (Esc)">${icon("x")}</button>
        </div>
        ${many ? `<button class="lb-nav prev" id="lb-prev" title="Previous">${icon("arrowLeft")}</button>
                  <button class="lb-nav next" id="lb-next" title="Next">${icon("arrowRight")}</button>` : ""}
        <figure class="lb-stage" id="lb-stage">
          <img data-api-src="${esc(shot.src || shot)}" alt="${esc(shot.name || "")}">
        </figure>
      </div>`);
    hydrateImages($("#lb"));

    const step = (by) => { at = (at + by + shots.length) % shots.length; draw(); };
    $("#lb-x").onclick = closeModal;
    if (many) {
      $("#lb-prev").onclick = (e) => { e.stopPropagation(); step(-1); };
      $("#lb-next").onclick = (e) => { e.stopPropagation(); step(1); };
    }
    // Fitted by default; one click inspects at full size, another goes back.
    $("#lb-zoom").onclick = (e) => {
      e.stopPropagation();
      $("#lb-stage").classList.toggle("actual");
    };
    $("#lb-stage").onclick = (e) => {
      if (e.target.tagName === "IMG") { e.stopPropagation(); $("#lb-stage").classList.toggle("actual"); }
    };
    // A click on the surround closes, as people expect of a picture opened full screen.
    $("#lb").onclick = (e) => { if (e.target.id === "lb" || e.target.id === "lb-stage") closeModal(); };

    document.onkeydown = (e) => {
      if (e.key === "Escape") { document.onkeydown = null; closeModal(); }
      else if (e.key === "ArrowLeft" && many) step(-1);
      else if (e.key === "ArrowRight" && many) step(1);
    };
  };
  draw();
}

function weakPasswordBanner() {
  if (!S.weakPassword) return "";
  return `<div class="warn-box mb" style="border-left-width:4px">
    ${icon("alert", "icon-sm")}
    <span><b>The administrator password is still the one from the setup guide.</b>
    Anyone who has read that guide can sign in as Internal Communication.
    <a href="#/profile" class="link-strong">Change it now</a>.</span></div>`;
}

function showSetupLink(who, email, url, days, sent) {
  // A set-up link is a capability, not a password: it lets its holder choose one. So the
  // wording has to push towards handing it to that person directly, and nowhere else.
  openModal(`
    <div class="overlay" id="sl-ov"><div class="modal narrow">
      <div class="modal-head"><h3>${icon("lock")}Set-up link for ${esc(who)}</h3></div>
      <div class="modal-body">
        <p class="muted" style="margin-top:0">Send this to <b>${esc(who)}</b>. They open it
          and choose their own password — you will not see it, and neither will the hub.
          They sign in afterwards as <b>${esc(email)}</b>.</p>
        <div class="field"><label>One-time link</label>
          <input id="sl-value" readonly value="${esc(url)}"
                 style="font-family:var(--mono);font-size:12.5px"></div>
        <div class="${sent ? "info-box" : "warn-box"}">${icon(sent ? "mail" : "alert", "icon-sm")}
          <span>${sent
            ? `It has also been e-mailed to them. The link works <b>once</b> and expires in ${days} days.`
            : `Automatic e-mail is off, so <b>nothing was sent</b> — pass this on yourself.
               The link works <b>once</b> and expires in ${days} days.`}</span></div>
      </div>
      <div class="modal-foot">
        <button class="btn" id="sl-copy">${icon("paperclip")}Copy link</button>
        <button class="btn btn-primary" id="sl-done">Done</button>
      </div>
    </div></div>`);
  $("#sl-copy").onclick = async () => {
    try {
      await navigator.clipboard.writeText(url);
      toast("Copied", `Send it to ${who} before closing this.`);
    } catch (e) {
      $("#sl-value").select();
      toast("Select and copy", "Your browser blocked the clipboard.", "warn");
    }
  };
  $("#sl-done").onclick = closeModal;
}

/* ------------------------------------------------------------------ auth */
async function boot() {
  try {
    const me = await api("/api/auth/me");
    if (me.user) {
      S.user = me.user; S.unread = me.unread || 0;
      S.scoped = !!me.scoped; S.scopeEventId = me.scope_event_id || null;
      S.archiveReadOnly = !!me.archive_viewer && me.user.role !== "admin";
      S.weakPassword = !!me.weak_password;
      S.uploadLimit = me.upload_limit_bytes || 0;
      S.canMoveDates = ["admin", "ic_user"].includes(me.user.role) && !me.scoped;
      S.settings = Object.assign(S.settings, me.settings || {});
      await loadLookups();
    }
  } catch (e) { /* not signed in */ }
  window.addEventListener("hashchange", route);
  route();
}

async function loadLookups() {
  try {
    const d = await api("/api/lookups");
    S.lookups = d;
    S.settings.currency = d.currency || S.settings.currency;
    S.settings.default_vat_rate = d.default_vat_rate;
  } catch (e) { /* ignore */ }
}

function renderLogin() {
  $("#root").innerHTML = `
  <div class="auth">
    <aside class="auth-brand">
      <img class="ab-logo" src="/assets/kabi-logo-white.png?v=2" alt="KABi">
      <div class="ab-eyebrow">Internal Communication</div>
      <h1 class="ab-title">Events, budgets and approvals in one place.</h1>
      <p class="ab-body">Create an event, add every vendor with its quotation, and send one clean
        request to your manager. Each vendor is approved or rejected on its own — with a reason, every time.</p>
      <ul class="ab-list">
        <li>${icon("check")}<span>Automatic budget calculation across all vendors</span></li>
        <li>${icon("check")}<span>Independent event and vendor decisions</span></li>
        <li>${icon("check")}<span>Execution tracking and a permanent event archive</span></li>
      </ul>
    </aside>
    <div class="auth-panel">
      <div class="auth-card">
        <h1>Sign in</h1>
        <p class="sub" id="li-sub">Enter your work e-mail to continue.</p>
        <form id="login-form">
          <div class="field mb"><label>Work e-mail</label>
            <input type="email" id="li-email" autocomplete="username" placeholder="name@kabi.ai" required></div>

          <div class="field mb hidden" id="li-code-wrap"><label>Sign-in code</label>
            <input id="li-code" inputmode="numeric" autocomplete="one-time-code" maxlength="6"
                   placeholder="6 digits"
                   style="letter-spacing:8px;font-size:20px;text-align:center;font-family:var(--mono)">
            <div class="hint" id="li-code-hint">Sent to your e-mail. Valid for 10 minutes.</div></div>

          <div class="field mb hidden" id="li-pass-wrap"><label>Password</label>
            <input type="password" id="li-pass" autocomplete="current-password" placeholder="••••••••">
            <div class="hint">This account is protected by a password.</div></div>

          <div id="li-err" class="warn-box mb hidden"></div>
          <button class="btn btn-primary btn-block" id="li-btn" type="submit">${icon("logout")}Continue</button>
        </form>
        <button class="btn btn-ghost btn-block mt hidden" id="li-again" type="button">Send a new code</button>
        <p class="muted" style="font-size:12.4px;margin-top:22px;line-height:1.7">
          ${icon("info", "icon-sm")} Approvers don't need to sign in — they review straight from the
          link in their e-mail. No password yet, or forgotten it? Ask the Internal
          Communication team for a set-up link; you choose your own password.</p>
      </div>
    </div>
  </div>`;

  // Three states on one form: ask for the e-mail, then either a code that was sent to
  // it or a password, depending on what the account uses.
  let stage = "email";
  const err = (message) => {
    const box = $("#li-err");
    box.classList.remove("hidden");
    box.innerHTML = `${icon("alert", "icon-sm")} ${esc(message)}`;
  };
  const setButton = (label, iconName) => {
    $("#li-btn").disabled = false;
    $("#li-btn").innerHTML = `${icon(iconName)}${label}`;
  };

  const enterApp = async (user) => {
    S.user = user;
    const me = await api("/api/auth/me");
    S.unread = me.unread || 0;
    S.settings = Object.assign(S.settings, me.settings || {});
    S.weakPassword = !!me.weak_password;
    await loadLookups();
    location.hash = "#/dashboard";
    route();
    toast("Welcome", `Signed in as ${user.name}`);
  };

  const askForCode = async () => {
    const out = await api("/api/auth/request-code", {
      method: "POST", body: { email: val("#li-email") },
    });
    stage = "code";
    $("#li-code-wrap").classList.remove("hidden");
    $("#li-code").required = true;
    $("#li-again").classList.remove("hidden");
    $("#li-sub").textContent = out.sent
      ? `We sent a code to ${out.email}. It is valid for ${out.minutes} minutes.`
      : "A code was created for this address.";
    $("#li-code-hint").textContent = out.sent
      ? `Valid for ${out.minutes} minutes, and can be used once.`
      : out.undelivered || "Ask the Internal Communication team for the code.";
    if (!out.sent) err(out.undelivered);
    setButton("Sign in", "logout");
    setTimeout(() => $("#li-code").focus(), 40);
  };

  $("#li-again").onclick = async () => {
    $("#li-err").classList.add("hidden");
    $("#li-again").disabled = true;
    try { await askForCode(); toast("Code sent", "Check your e-mail."); }
    catch (e) { err(e.message); }
    $("#li-again").disabled = false;
  };

  $("#login-form").onsubmit = async (e) => {
    e.preventDefault();
    const btn = $("#li-btn");
    btn.disabled = true;
    btn.innerHTML = stage === "email" ? "Sending…" : "Signing in…";
    $("#li-err").classList.add("hidden");
    try {
      if (stage === "email") {
        // Try the password route first: an administrator has one, and should not be
        // sent a code they do not need.
        try {
          const d = await api("/api/auth/login", {
            method: "POST", body: { email: val("#li-email") },
          });
          return await enterApp(d.user);            // no secret required at all
        } catch (probe) {
          if (probe.data && probe.data.needs_password) {
            stage = "password";
            $("#li-pass-wrap").classList.remove("hidden");
            $("#li-pass").required = true;
            $("#li-sub").textContent = "This account uses a password.";
            setButton("Sign in", "logout");
            setTimeout(() => $("#li-pass").focus(), 40);
            return;
          }
          if (probe.data && probe.data.needs_code) return await askForCode();
          throw probe;
        }
      }

      if (stage === "password") {
        const d = await api("/api/auth/login", {
          method: "POST",
          body: { email: val("#li-email"), password: $("#li-pass").value },
        });
        return await enterApp(d.user);
      }

      const d = await api("/api/auth/verify-code", {
        method: "POST", body: { email: val("#li-email"), code: val("#li-code") },
      });
      return await enterApp(d.user);
    } catch (e2) {
      err(e2.message);
      setButton(stage === "email" ? "Continue" : "Sign in", "logout");
    }
  };
}

/* ------------------------------------------------- choosing your own password */
/* Reached from a one-time link. Nobody in IC sets a password, so this page is the only
   way one ever comes into being -- which makes its error states the whole story: a link
   that is spent or expired has to say so, and say who to ask. */
async function viewSetPassword(token) {
  $("#root").innerHTML = `<div class="auth"><div class="auth-panel" style="flex:1">
    <div class="auth-card"><h1>One moment</h1>
      <p class="sub">Checking your link…</p></div></div></div>`;

  let who;
  try {
    who = await api("/api/setup/" + token);
  } catch (e) {
    $("#root").innerHTML = `<div class="auth"><div class="auth-panel" style="flex:1">
      <div class="auth-card">
        <h1>This link cannot be used</h1>
        <div class="warn-box mb">${icon("alert", "icon-sm")} ${esc(e.message)}</div>
        <p class="muted" style="font-size:13px">Set-up links work once and expire on their
          own. Ask the Internal Communication team for a fresh one — it takes them a moment.</p>
        <a class="btn btn-block mt" href="#/">Back to sign in</a>
      </div></div></div>`;
    return;
  }

  $("#root").innerHTML = `
  <div class="auth">
    <aside class="auth-brand">
      <img class="ab-logo" src="/assets/kabi-logo-white.png?v=2" alt="KABi">
      <div class="ab-eyebrow">Internal Communication</div>
      <h1 class="ab-title">Choose your password.</h1>
      <p class="ab-body">You pick it, and only you know it. Internal Communication cannot
        see it, cannot look it up, and cannot set it for you — if you ever forget it, they
        send you a fresh link like this one and you choose again.</p>
      <ul class="ab-list">
        <li>${icon("check")}<span>At least ${who.min_length} characters</span></li>
        <li>${icon("check")}<span>Not your name and not your e-mail address</span></li>
        <li>${icon("check")}<span>Long beats complicated — a short phrase works well</span></li>
      </ul>
    </aside>
    <div class="auth-panel">
      <div class="auth-card">
        <h1>Welcome, ${esc((who.name || "").split(" ")[0])}</h1>
        <p class="sub">You will sign in as <b>${esc(who.email)}</b>.</p>
        <form id="sp-form">
          <div class="field mb"><label>New password</label>
            <input type="password" id="sp-pass" autocomplete="new-password"
                   placeholder="At least ${who.min_length} characters" required></div>
          <div class="field mb"><label>Type it again</label>
            <input type="password" id="sp-again" autocomplete="new-password"
                   placeholder="The same again" required></div>
          <div id="sp-err" class="warn-box mb hidden"></div>
          <button class="btn btn-primary btn-block" id="sp-btn" type="submit">
            ${icon("lock")}Set my password and sign in</button>
        </form>
        <p class="muted" style="font-size:12.4px;margin-top:22px;line-height:1.7">
          ${icon("info", "icon-sm")} Nobody will ever ask you for this password — not by
          e-mail, not in a message. If someone does, it is not us.</p>
      </div>
    </div>
  </div>`;

  const err = (message) => {
    const box = $("#sp-err");
    box.classList.remove("hidden");
    box.innerHTML = `${icon("alert", "icon-sm")} ${esc(message)}`;
  };

  $("#sp-form").onsubmit = async (ev) => {
    ev.preventDefault();
    $("#sp-err").classList.add("hidden");
    const pass = val("#sp-pass");
    if (pass !== val("#sp-again")) { err("The two passwords do not match."); return; }
    $("#sp-btn").disabled = true;
    $("#sp-btn").innerHTML = `${icon("lock")}Setting it…`;
    try {
      const d = await api("/api/setup/" + token, {
        method: "POST", body: { password: pass, confirm: val("#sp-again") },
      });
      S.user = d.user;
      const me = await api("/api/auth/me");
      S.unread = me.unread || 0;
      S.settings = Object.assign(S.settings, me.settings || {});
      S.weakPassword = !!me.weak_password;
      S.canMoveDates = ["admin", "ic_user"].includes(d.user.role) && !me.scoped;
      await loadLookups();
      location.hash = "#/dashboard";
      route();
      toast("You are in", "Your password is set. Only you know it.");
    } catch (e) {
      err(e.message);
      $("#sp-btn").disabled = false;
      $("#sp-btn").innerHTML = `${icon("lock")}Set my password and sign in`;
    }
  };
}

/* ------------------------------------------------- passwordless review link */
async function viewReviewLink(token) {
  let r;
  try {
    r = (await api("/api/review/" + token)).review;
  } catch (e) {
    $("#root").innerHTML = `<div class="auth"><aside class="auth-brand">
        <img class="ab-logo" src="/assets/kabi-logo-white.png?v=2" alt="KABi">
        <div class="ab-eyebrow">Internal Communication</div>
        <h1 class="ab-title">Events Approval Hub</h1></aside>
      <div class="auth-panel"><div class="auth-card">
        <h1>Link not available</h1>
        <div class="warn-box mb">${icon("alert", "icon-sm")} ${esc(e.message)}</div>
        <p class="sub">This usually means one of the following:</p>
        <ul class="muted" style="font-size:13px;padding-left:18px;line-height:1.9;margin:0 0 20px">
          <li>The event was <b>resubmitted</b> — a newer e-mail replaced this link.</li>
          <li>A decision has already been recorded, so the link was retired.</li>
          <li>The request was withdrawn or cancelled by the requester.</li>
          <li>The link is older than 45 days.</li>
        </ul>
        <p class="muted" style="font-size:13px">Ask the requester to resend the request — they can do that
          from the event page in one click. If you have an account, sign in to see everything assigned to you.</p>
        <a class="btn btn-primary btn-block mt" href="#/dashboard" data-act="reload-app">Go to sign in</a>
      </div></div></div>`;
    return;
  }

  // this browser already confirmed who they are — walk straight in
  if (r.remembered) {
    $("#root").innerHTML = `<div class="loading"><div class="spinner"></div>
      Welcome back, ${esc(r.remembered.name)} — opening ${esc(r.event_number)}…</div>`;
    try {
      const d = await api(`/api/review/${token}/continue`, { method: "POST" });
      S.user = d.user; S.scoped = true; S.scopeEventId = d.event_id;
      const me = await api("/api/auth/me");
      S.unread = me.unread || 0; S.settings = Object.assign(S.settings, me.settings || {});
      await loadLookups();
      location.hash = `#/events/${d.event_id}/approve`;
      route();
      return;
    } catch (e) { /* fall through to the confirmation form */ }
  }

  $("#root").innerHTML = `
  <div class="auth">
    <aside class="auth-brand">
      <img class="ab-logo" src="/assets/kabi-logo-white.png?v=2" alt="KABi">
      <div class="ab-eyebrow">Approval request</div>
      <h1 class="ab-title">${esc(r.event_name)}</h1>
      <p class="ab-body">${esc(r.event_number)} · ${fmtDate(r.event_date)}<br>
        Requested by ${esc(r.requested_by)}</p>
      <ul class="ab-list">
        <li>${icon("check")}<span>No password required — confirmed once, then remembered on this device</span></li>
        <li>${icon("check")}<span>Approve or reject the event and each vendor separately</span></li>
        <li>${icon("check")}<span>This link opens this event only and expires in 45 days</span></li>
      </ul>
    </aside>
    <div class="auth-panel">
      <div class="auth-card">
        <h1>Confirm it's you</h1>
        <p class="sub">This request was sent to <b class="mono">${esc(r.email_hint)}</b>.
          Enter your name and that e-mail address to open the approval page.
          <b>You'll only be asked once on this device.</b></p>
        ${r.awaiting ? "" : `<div class="note-box mb">${icon("info", "icon-sm")}
          A decision has already been recorded for this event. You can still open it to review the details.</div>`}
        <form id="rv-form">
          <div class="field mb"><label>Your full name <span class="req">*</span></label>
            <input id="rv-name" placeholder="e.g. John Smith" autocomplete="name" required></div>
          <div class="field mb"><label>Your work e-mail <span class="req">*</span></label>
            <input type="email" id="rv-mail" placeholder="name@kabi.ai" autocomplete="email" required></div>
          <div id="rv-err" class="warn-box mb hidden"></div>
          <button class="btn btn-primary btn-block" id="rv-btn" type="submit">
            ${icon("clipboard")}Open the approval page</button>
        </form>
        <div class="demo-box" style="border-style:solid">
          <h4>${icon("lock", "icon-sm")} Why we ask</h4>
          <p class="muted" style="font-size:12.4px;margin:0">Your name and e-mail are recorded in the approval
            history so the decision is properly attributed. The e-mail must match the address this request was
            sent to. Prefer signing in normally?
            <a href="#/dashboard" data-act="reload-app">Use your account</a>.</p>
        </div>
      </div>
    </div>
  </div>`;

  $("#rv-form").onsubmit = async (ev) => {
    ev.preventDefault();
    const btn = $("#rv-btn"); btn.disabled = true; btn.innerHTML = "Opening…";
    try {
      const d = await api(`/api/review/${token}/enter`, {
        method: "POST", body: { name: val("#rv-name"), email: val("#rv-mail") },
      });
      S.user = d.user; S.scoped = true;
      const me = await api("/api/auth/me");
      S.unread = me.unread || 0; S.settings = Object.assign(S.settings, me.settings || {});
      await loadLookups();
      location.hash = `#/events/${d.event_id}/approve`;
      route();
      toast("Welcome", `Reviewing as ${d.user.name}`);
    } catch (err) {
      const box = $("#rv-err"); box.classList.remove("hidden"); box.textContent = err.message;
      btn.disabled = false; btn.innerHTML = `${icon("clipboard")}Open the approval page`;
    }
  };
}

/* ----------------------------------------------------------------- shell */
function navItems() {
  // A review-link session only ever sees the one event it was issued for.
  if (S.scoped) return [{ h: `#/events/${S.scopeEventId}/approve`, i: "clipboard", t: "Review event", k: "approve" }];
  const r = S.user.role;
  const items = [{ h: "#/dashboard", i: "dashboard", t: "Dashboard", k: "dashboard" }];
  if (S.archiveReadOnly)
    items.push({ h: "#/archive", i: "photo", t: "Completed activities", k: "events", cta: true });
  if (r === "manager" || r === "admin")
    items.push({ h: "#/approvals", i: "clipboard", t: "Approvals", k: "approvals" });
  items.push({ h: "#/events", i: "calendar", t: "Events", k: "events" });
  if (r === "ic_user" || r === "admin")
    items.push({ h: "#/events/new", i: "plus", t: "New Event", k: "new", cta: true });
  if (r === "admin")
    items.push({ h: "#/admin", i: "settings", t: "Administration", k: "admin" });
  items.push({ h: "#/notifications", i: "bell", t: "Notifications", k: "notifications" });
  items.push({ h: "#/profile", i: "user", t: "Profile", k: "profile" });
  return items;
}

function shell(viewHtml) {
  const roleText = roleLabel(S.user);
  return `
  <header class="topbar">
    <div class="brand">
      <img class="brand-logo" src="/assets/kabi-logo-white.png?v=2" alt="KABi">
      <div class="brand-sep"></div>
      <div class="brand-txt"><b>Events Approval Hub</b><span>Internal Communication</span></div>
    </div>
    <nav class="nav">
      ${navItems().map(n => `<a href="${n.h}" class="${S.route.name === n.k ? "on" : ""}${n.cta ? " cta" : ""}">
        ${icon(n.i, "icon-sm")}${n.t}</a>`).join("")}
    </nav>
    <div class="top-actions">
      ${S.scoped ? `<span class="badge b-completed" style="background:rgba(255,255,255,.16);color:#fff;border-color:rgba(255,255,255,.3)">
        ${icon("lock", "icon-sm")}Secure review link</span>`
      : `<button class="icon-btn" data-act="bell" title="Notifications">${icon("bell")}
        ${S.unread ? `<span class="dot">${S.unread > 99 ? "99+" : S.unread}</span>` : ""}</button>`}
      <div class="user-menu">
        <button class="avatar" data-act="usermenu" title="${esc(S.user.name)}">${esc(initials(S.user.name))}</button>
        ${S.menuOpen ? `<div class="menu" id="umenu">
          <div class="mh"><b>${esc(S.user.name)}</b><span>${esc(S.user.email)}</span>
            <span class="badge b-neutral" style="margin-top:6px">${esc(roleText)}</span></div>
          ${S.scoped ? `<div class="mh" style="border-bottom:0;padding-top:0">
              <span class="muted" style="font-size:12px">You opened this from an approval e-mail.
              This browser is remembered, so future links open without asking again.</span></div>
            <button data-act="forget-device">${icon("eyeOff")}Forget this device</button>` : `
          <button data-act="go" data-href="#/profile">${icon("user")}My profile</button>
          <button data-act="go" data-href="#/notifications">${icon("bell")}Notifications${S.unread ? ` <span class="pill-count">${S.unread}</span>` : ""}</button>`}
          <button class="danger" data-act="logout">${icon("logout")}${S.scoped ? "Leave review" : "Sign out"}</button>
        </div>` : ""}
      </div>
    </div>
  </header>
  <main class="main" id="view">${viewHtml}</main>`;
}

function loadingView() { return `<div class="loading"><div class="spinner"></div>Loading…</div>`; }

/* ---------------------------------------------------------------- router */
function parseHash() {
  const h = (location.hash || "#/dashboard").replace(/^#\/?/, "");
  const p = h.split("/").filter(Boolean);
  if (!p.length) return { name: "dashboard" };
  if (p[0] === "events") {
    if (p[1] === "new") return { name: "new" };
    if (p[1] && p[2] === "edit") return { name: "edit", id: p[1] };
    if (p[1] && p[2] === "approve") return { name: "approve", id: p[1] };
    if (p[1]) return { name: "detail", id: p[1] };
    return { name: "events" };
  }
  if (p[0] === "admin") { if (p[1]) S.adminTab = p[1]; return { name: "admin" }; }
  // History used to be its own page; it is now the Archive tab inside Events.
  if (p[0] === "history" || p[0] === "archive") { S.eventsTab = "archive"; return { name: "events" }; }
  const known = ["dashboard", "approvals", "notifications", "profile"];
  return { name: known.includes(p[0]) ? p[0] : "dashboard" };
}

async function route() {
  // Passwordless review link straight from the approval e-mail — no sign-in required.
  const m = (location.hash || "").match(/^#\/review\/([A-Za-z0-9_\-]{20,120})$/);
  if (m) { await viewReviewLink(m[1]); return; }
  // Choosing a password needs no session, and must work even for someone already signed
  // in as somebody else on this browser.
  const sp = (location.hash || "").match(/^#\/set-password\/([A-Za-z0-9_\-]{20,120})$/);
  if (sp) { await viewSetPassword(sp[1]); return; }
  if (!S.user) { renderLogin(); return; }
  S.route = parseHash();
  S.menuOpen = false;
  $("#root").innerHTML = shell(loadingView());
  window.scrollTo(0, 0);
  try {
    const views = {
      dashboard: viewDashboard, events: viewEvents, new: viewNewEvent, detail: viewDetail,
      edit: viewEdit, approve: viewApprove, approvals: viewApprovals,
      notifications: viewNotifications, profile: viewProfile, admin: viewAdmin,
    };
    const fn = views[S.route.name] || viewDashboard;
    await fn();
    hydrateImages();          // pictures are fetched with our headers, not as bare <img>
  } catch (e) {
    $("#view").innerHTML = `<div class="card card-pad"><div class="empty">
      ${icon("alert", "icon-lg")}<h4>${esc(e.message)}</h4>
      <p class="muted">You may not have permission to open this page, or it no longer exists.</p>
      <a class="btn mt" href="#/dashboard">${icon("arrowLeft")}Back to dashboard</a></div></div>`;
  }
}
function render() { if (!S.user) return renderLogin(); route(); }
function setView(html) {
  html = weakPasswordBanner() + html; $("#root").innerHTML = shell(html); }

/* ------------------------------------------------------------- dashboard */
async function viewDashboard() {
  const period = new URLSearchParams();
  if (S.dashYear !== "all") period.set("year", S.dashYear);
  if (S.dashMonth !== "all") period.set("month", S.dashMonth);
  const [st, ev] = await Promise.all([
    api("/api/stats?" + period.toString()),
    api("/api/events?" + period.toString()),
  ]);
  const s = st.stats;
  const events = ev.events.slice(0, 8);
  const isMgr = S.user.role === "manager" || S.user.role === "admin";

  setView(`
  <div class="page-head">
    <div><h1>Welcome, ${esc(S.user.name.split(" ")[0])}</h1>
      <p>${isMgr ? "Approval requests assigned to you and the events you can see." : "Your Internal Communication events, budgets and approvals."}</p></div>
    <div class="head-actions">
      <div class="period-pick">
        ${icon("calendar", "icon-sm")}
        <select id="dash-year" data-act="dash-period">
          <option value="all" ${S.dashYear === "all" ? "selected" : ""}>All years</option>
          ${(s.years || []).map(y => `<option value="${esc(y)}"
            ${String(S.dashYear) === String(y) ? "selected" : ""}>${esc(y)}</option>`).join("")}
        </select>
        <select id="dash-month" data-act="dash-period" ${S.dashYear === "all" ? "disabled" : ""}>
          <option value="all" ${S.dashMonth === "all" ? "selected" : ""}>All months</option>
          ${MONTH_NAMES.map((m, i) => `<option value="${i + 1}"
            ${String(S.dashMonth) === String(i + 1) ? "selected" : ""}>${m}</option>`).join("")}
        </select>
      </div>
      ${isMgr && s.awaiting_my_decision ? `<a class="btn btn-cyan" href="#/approvals">${icon("clipboard")}Review ${s.awaiting_my_decision} request${s.awaiting_my_decision > 1 ? "s" : ""}</a>` : ""}
    </div>
  </div>

  <div class="stat-grid">
    ${statCard("c-total", "calendar", "Total Events", s.total, `${s.draft} draft`)}
    ${statCard("c-pending", "hourglass", "Pending Approval", s.pending, isMgr ? `${s.awaiting_my_decision || 0} awaiting you` : "awaiting a decision")}
    ${statCard("c-approved", "circleCheck", "Approved", s.approved, `${s.partially} partially approved`)}
    ${statCard("c-rejected", "circleX", "Rejected", s.rejected, `${s.cancelled} cancelled`)}
    ${statCard("c-budget", "coin", "Total Budget", money(s.total_budget, false), S.settings.currency, true)}
    ${statCard("c-info", "wallet", "Approved Budget", money(s.approved_budget, false), S.settings.currency, true)}
  </div>

  <div class="card">
    <div class="card-head"><h3>${icon("list")}Recent Events</h3>
      <a class="btn btn-sm" href="#/events">View all${icon("arrowRight", "icon-sm")}</a></div>
    ${events.length ? eventsTable(events) : emptyState("No events yet", "Create your first event to get started.",
    S.user.role !== "manager" ? `<a class="btn btn-primary mt" href="#/events/new">${icon("plus")}New Event</a>` : "")}
  </div>`);
}

function statCard(cls, ic, label, value, sub, small) {
  return `<div class="stat ${cls}"><div class="accent"></div>
    <div class="lbl">${icon(ic, "icon-sm")}${esc(label)}</div>
    <div class="val ${small ? "sm" : ""}">${esc(value)}</div>
    <div class="sub">${esc(sub || "")}</div></div>`;
}
function emptyState(title, msg, extra) {
  return `<div class="empty">${icon("calendar", "icon-lg")}<h4>${esc(title)}</h4>
    <p class="muted">${esc(msg)}</p>${extra || ""}</div>`;
}

/* the approval chain: 1st -> 2nd -> 3rd, showing where the request has reached */
function chainStrip(e, compact) {
  const list = e.approvers || [];
  if (!list.length) return "";
  const levels = [...new Set(list.map(a => a.level || 1))].sort((x, y) => x - y);
  return `<div class="chain ${compact ? "compact" : ""}">
    ${levels.map((lv, i) => {
    const at = list.filter(a => (a.level || 1) === lv);
    const done = at.some(a => a.status === "approved");
    const stopped = at.some(a => a.status === "rejected");
    const here = e.current_level === lv && ["pending_approval", "pending_vendor_approval"].includes(e.status);
    const cls = stopped ? "no" : done ? "ok" : here ? "now" : "wait";
    const isFinal = at.some(a => a.is_final);
    return `${i ? `<span class="chain-arrow">${icon("chevronRight", "icon-sm")}</span>` : ""}
        <div class="chain-step ${cls}" title="${esc(at.map(a => a.name).join(", "))}">
          <span class="chain-dot">${stopped ? icon("x", "icon-sm") : done ? icon("check", "icon-sm") : lv}</span>
          <span class="chain-txt"><b>${esc(ordinal(lv))} approver${isFinal ? " · final" : ""}</b>
            <span>${esc(at.map(a => a.name.split(" ")[0]).join(", "))}${here ? " — deciding now" : done ? " — approved" : stopped ? " — rejected" : ""}</span></span>
        </div>`;
  }).join("")}
  </div>`;
}

function isMyApproval(e) {
  return (e.approvers || []).some(a => a.id === S.user.id);
}

/* A budget is not a figure until the chain has finished with it. While the request is
   still moving, any number here is one option's estimate dressed up as the answer -- and
   a later approver can pick a different package or drop a vendor. So it is shown as
   undecided until there is a decision, with the range where there are options to compare. */
function budgetCell(e) {
  const settled = !["draft", "pending_approval"].includes(e.status);
  // Once the chain has finished, the figure that matters is what was actually approved:
  // the estimate still counts vendors the approver threw out, and a package they did
  // not take. A rejected event settles at nothing, which is also an answer.
  if (settled) {
    // A turned-down or cancelled event commits nothing, so its estimate is not its
    // budget -- showing 410.00 against "Rejected" reads as money that will be spent.
    if (["rejected", "cancelled"].includes(e.status)) return money(0, false);
    return money(e.approved_budget, false);
  }
  const totals = (e.option_totals || (e.options || []).map(o => o.total) || [])
    .map(Number).filter(n => n > 0);
  if (totals.length > 1) {
    const lo = Math.min(...totals), hi = Math.max(...totals);
    if (lo !== hi) {
      return `<span class="faint" title="Not settled until the final approval">${
        money(lo, false)} – ${money(hi, false)}</span>`;
    }
  }
  return `<span class="faint" title="Not settled until the final approval">—</span>`;
}

function approverCell(e) {
  const list = e.approvers || [];
  if (!list.length) return "—";
  return esc(list[0].name) + (list.length > 1
    ? ` <span class="pill-count" title="${esc(list.slice(1).map(a => a.name).join(", "))}">+${list.length - 1}</span>`
    : "");
}

function eventsTable(events) {
  return `<div class="table-wrap"><table class="tbl">
    <thead><tr>
      <th>Event ID</th><th>Event Name</th><th>Event Date</th><th>Type</th><th class="right">Total Budget</th>
      <th>Event Status</th><th>Vendors</th><th>Approver</th><th>Created</th><th></th>
    </tr></thead><tbody>
    ${events.map(e => `<tr>
      <td class="mono nowrap"><a class="link-strong" href="#/events/${e.id}">${esc(e.event_number)}</a></td>
      <td><a class="link-strong" href="#/events/${e.id}">${esc(e.event_name)}</a>
          <div class="faint" style="font-size:11.5px">${icon("mapPin", "icon-sm")} ${esc(e.location || "—")}</div></td>
      <td class="nowrap">${fmtDate(e.event_date)}</td>
      <td>${esc(e.event_type || "—")}</td>
      <td class="right mono nowrap">${budgetCell(e)}</td>
      <td>${statusBadge(e.status)}</td>
      <td>${vendorSummaryBadge(e.vendor_summary)}</td>
      <td class="nowrap">${approverCell(e)}</td>
      <td class="nowrap faint">${fmtDate(e.created_at)}</td>
      <td><div class="row-actions">
        <a class="btn btn-sm" href="#/events/${e.id}" title="View">${icon("eye", "icon-sm")}</a>
        ${e.status === "draft" && (e.created_by === S.user.id || S.user.role === "admin")
      ? `<a class="btn btn-sm" href="#/events/${e.id}/edit" title="Edit">${icon("pencil", "icon-sm")}</a>` : ""}
        ${(e.status === "pending_approval" || e.status === "pending_vendor_approval") && isMyApproval(e)
      ? `<a class="btn btn-sm btn-cyan" href="#/events/${e.id}/approve">${icon("clipboard", "icon-sm")}Review</a>` : ""}
      </div></td></tr>`).join("")}
    </tbody></table></div>`;
}

/* ---------------------------------------------------------------- events */
function filterQuery() {
  const f = S.filters, q = new URLSearchParams();
  if (f.search) q.set("search", f.search);
  if (f.status !== "all") q.set("status", f.status);
  if (f.vendor_status !== "all") q.set("vendor_status", f.vendor_status);
  if (f.event_type !== "all") q.set("event_type", f.event_type);
  if (f.approver) q.set("approver", f.approver);
  if (f.date_from) q.set("date_from", f.date_from);
  if (f.date_to) q.set("date_to", f.date_to);
  // the history can be narrowed to activities, recaps or quarterly updates
  if (S.recordKind && S.recordKind !== "all") q.set("kind", S.recordKind);
  return q.toString();
}

const ARCHIVE_STATUSES = ["completed", "fully_approved", "partially_approved", "rejected", "cancelled"];

async function viewEvents() {
  const d = await api("/api/events?" + filterQuery());
  const f = S.filters;
  const archive = S.eventsTab === "archive";
  const active = d.events.filter(e => !ARCHIVE_STATUSES.includes(e.status) || !e.executed);
  const closed = d.events.filter(e => ARCHIVE_STATUSES.includes(e.status));
  const list = archive ? closed : d.events.filter(e => !ARCHIVE_STATUSES.includes(e.status));

  setView(`
  <div class="page-head">
    <div><h1>Events</h1><p>${archive
      ? "Completed activities — approved, rejected, cancelled or executed. Open any record to see its full details, vendor decisions and timeline."
      : "Events in progress — drafts and requests awaiting a decision."}</p></div>
    <div class="head-actions">
      ${archive && ["admin", "ic_user"].includes(S.user.role)
        ? `<button class="btn btn-primary" data-act="add-completed">${icon("plus")}Add to history</button>` : ""}
      ${["admin", "ic_user"].includes(S.user.role)
        ? `<button class="btn" data-act="export-xlsx">${icon("fileExport")}Export to Excel</button>` : ""}
      ${S.user.role === "admin" ? `<button class="btn btn-ghost" data-act="export">${icon("fileExport")}CSV</button>` : ""}
    </div>
  </div>

  <div class="tabs">
    <button data-act="events-tab" data-tab="active" class="${archive ? "" : "on"}">
      ${icon("calendar", "icon-sm")}Ongoing <span class="pill-count">${d.events.length - closed.length}</span></button>
    <button data-act="events-tab" data-tab="archive" class="${archive ? "on" : ""}">
      ${icon("checks", "icon-sm")}Completed <span class="pill-count">${closed.length}</span></button>
  </div>

  ${archive ? `${archiveSummary(closed)}
    <div class="seg mb" style="flex-wrap:wrap">
      ${RECORD_KINDS.map(k => `<button data-act="kind-filter" data-kind="${k.k}"
        class="${S.recordKind === k.k ? "on" : ""}">${icon(k.i, "icon-sm")}${k.t}</button>`).join("")}
    </div>
    <div class="seg mb">
      <button data-act="arch-view" data-v="calendar" class="${S.archiveView === "list" ? "" : "on"}">
        ${icon("calendar", "icon-sm")}Calendar</button>
      <button data-act="arch-view" data-v="list" class="${S.archiveView === "list" ? "on" : ""}">
        ${icon("list", "icon-sm")}List</button>
    </div>` : ""}

  ${archive && S.archiveView !== "list" ? `<div id="cal-host"></div>` : ""}

  <div class="card card-pad mb ${archive && S.archiveView !== "list" ? "hidden" : ""}">
    <div class="toolbar">
      <div class="search">${icon("search", "icon-sm")}
        <input id="f-search" placeholder="Search by event name, event ID, location or vendor name…" value="${esc(f.search)}"></div>
      <select id="f-status">
        <option value="all">All event statuses</option>
        ${Object.keys(STATUS).map(k => `<option value="${k}" ${f.status === k ? "selected" : ""}>${STATUS[k].label}</option>`).join("")}
      </select>
      <select id="f-vstatus">
        <option value="all">All vendor statuses</option>
        <option value="pending" ${f.vendor_status === "pending" ? "selected" : ""}>Has pending vendors</option>
        <option value="approved" ${f.vendor_status === "approved" ? "selected" : ""}>All vendors approved</option>
        <option value="rejected" ${f.vendor_status === "rejected" ? "selected" : ""}>Has rejected vendors</option>
        <option value="mixed" ${f.vendor_status === "mixed" ? "selected" : ""}>Mixed decisions</option>
      </select>
      <select id="f-type">
        <option value="all">All event types</option>
        ${S.lookups.event_types.map(t => `<option ${f.event_type === t ? "selected" : ""}>${esc(t)}</option>`).join("")}
      </select>
      <select id="f-approver">
        <option value="">All approvers</option>
        ${S.lookups.approvers.map(a => `<option value="${a.id}" ${String(f.approver) === String(a.id) ? "selected" : ""}>${esc(a.name)}</option>`).join("")}
      </select>
      <input type="date" id="f-from" value="${esc(f.date_from)}" title="Event date from">
      <input type="date" id="f-to" value="${esc(f.date_to)}" title="Event date to">
      <button class="btn btn-ghost btn-sm" data-act="clear-filters">${icon("refresh", "icon-sm")}Reset</button>
    </div>
    ${list.length ? "" : `<div class="empty">${icon("search", "icon-lg")}
      <h4>${archive ? "Nothing completed yet" : "No ongoing events"}</h4>
      <p class="muted">${archive
      ? "Events appear here once they are approved, rejected, cancelled or executed."
      : "Create an event, or check the Completed tab for closed ones."}</p></div>`}
  </div>

  ${list.length && !(archive && S.archiveView !== "list")
      ? (archive ? archiveList(list) : `<div class="card">${eventsTable(list)}</div>`) : ""}`);

  if (archive && S.archiveView !== "list") { renderCalendar(); return; }

  const apply = () => {
    S.filters = {
      search: val("#f-search"), status: val("#f-status"), vendor_status: val("#f-vstatus"),
      event_type: val("#f-type"), approver: val("#f-approver"),
      date_from: val("#f-from"), date_to: val("#f-to"),
    };
    viewEvents();
  };
  let t;
  $("#f-search").oninput = () => { clearTimeout(t); t = setTimeout(apply, 350); };
  ["#f-status", "#f-vstatus", "#f-type", "#f-approver", "#f-from", "#f-to"].forEach(s => { $(s).onchange = apply; });
}

/* ------------------------------------------------------------- new event */
async function viewNewEvent() {
  if (S.user.role === "manager") throw new Error("Managers cannot create events.");
  setView(`
  <div class="page-head">
    <div><h1>New Event</h1><p>Step 1 of 2 — event information. You will add vendors and quotations next.</p></div>
    <a class="btn btn-ghost" href="#/events">${icon("arrowLeft")}Cancel</a>
  </div>
  <div class="card">
    <div class="card-head"><h3>${icon("calendar")}Event Information</h3></div>
    <div class="card-pad">${eventFormFields({})}</div>
    <div class="sticky-foot">
      <span class="muted" style="font-size:12.5px">${icon("info", "icon-sm")} A unique Event ID (IC-${new Date().getFullYear()}-000) is generated automatically.</span>
      <button class="btn btn-primary" data-act="create-event">${icon("check")}Create Draft & Add Vendors</button>
    </div>
  </div>`);
  bindApproverPicker();
  bindDeliveryInputs();
}

/* The standing names keep the order they are configured in, so the events list goes on
   showing the same first name; anyone ticked on top of them follows after. Without this
   the order came from however the picker happened to list people. */
function orderedRecordApprovers(ids) {
  const standing = (S.lookups.record_approvers || []).filter(id => ids.includes(id));
  return [...standing, ...ids.filter(id => !standing.includes(id))];
}

/* The ticked ids in one of the approver pickers, in the order they are shown. */
function pickedApprovers(sel) {
  const host = document.querySelector(sel);
  if (!host) return [];
  return [...host.querySelectorAll('input[type="checkbox"]')]
    .filter(c => c.checked).map(c => Number(c.value));
}

function approverRow(a, on) {
  return `<label class="picker-row ${on ? "on" : ""}">
    <input type="checkbox" value="${a.id}" ${on ? "checked" : ""}>
    <span class="pk-av">${esc(initials(a.name))}</span>
    <span class="grow"><b>${esc(a.name)}
      ${a.approver_level ? `<span class="badge b-partial" style="font-size:10px;padding:1px 7px">${esc(ordinal(a.approver_level))}</span>` : ""}
      ${a.is_final_approver ? `<span class="badge b-approved" style="font-size:10px;padding:1px 7px">final</span>` : ""}
      ${a.invited ? `<span class="badge b-neutral" style="font-size:10px;padding:1px 7px">invited</span>` : ""}</b>
      <span class="faint">${esc(a.job_title || a.department || "Approver")} · ${esc(a.email)}</span></span>
    ${icon("check", "pk-tick")}</label>`;
}

function eventFormFields(e) {
  const types = S.lookups.event_types;
  return `
  <div class="form-grid">
    <div class="field full"><label>Event Name <span class="req">*</span></label>
      <input id="ev-name" value="${esc(e.event_name || "")}" placeholder="e.g. Annual Gathering 2026" maxlength="200"></div>
    <div class="field"><label>Event Type</label>
      <select id="ev-type"><option value="">Select a type…</option>
        ${types.map(t => `<option ${e.event_type === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
    <div class="field"><label>Approver / Manager <span class="req">*</span></label>
      <div class="picker" id="ev-approvers">
        ${S.lookups.approvers.map(a => approverRow(a, (e.approvers || []).some(x => x.id === a.id))).join("")
      || `<div class="muted" style="padding:10px">No approvers yet — add one below.</div>`}
      </div>
      <div class="add-approver">
        <button type="button" class="btn btn-sm btn-ghost" data-act="show-add-approver">
          ${icon("plus", "icon-sm")}Add someone who isn't listed</button>
        <div class="hidden" id="ap-form">
          <div class="toolbar" style="margin:8px 0 0">
            <input id="ap-name" placeholder="Full name" style="flex:1 1 150px;padding:9px 11px;border:1px solid var(--line-2);border-radius:10px">
            <input id="ap-mail" type="email" placeholder="name@kabi.ai" style="flex:1 1 180px;padding:9px 11px;border:1px solid var(--line-2);border-radius:10px">
            <button type="button" class="btn btn-sm btn-cyan" data-act="add-approver">${icon("check", "icon-sm")}Add</button>
          </div>
          <div class="hint">They don't need an account — the approval e-mail lets them review and decide
            without a password.</div>
          <div id="ap-err" class="warn-box mt hidden"></div>
        </div>
      </div>
      <div class="hint">Everyone you tick receives the approval e-mail with their own review link.
        The first one to submit a decision records it for all.</div></div>
    <div class="field"><label>Event Date <span class="req">*</span></label>
      <input type="date" id="ev-date" value="${esc(e.event_date || "")}"></div>
    <div class="field"><label>Expected Attendees</label>
      <input type="number" min="0" id="ev-att" value="${e.expected_attendees ?? ""}" placeholder="e.g. 150"></div>
    <div class="field"><label>Start Time</label><input type="time" id="ev-start" value="${esc(e.start_time || "")}"></div>
    <div class="field"><label>End Time</label><input type="time" id="ev-end" value="${esc(e.end_time || "")}"></div>
    <div class="field full"><label>Location <span class="req">*</span></label>
      <input id="ev-loc" value="${esc(e.location || "")}" placeholder="e.g. KABi HQ — Main Hall, Riyadh" maxlength="200"></div>
    <div class="field full"><label>Event Description <span class="req">*</span></label>
      <textarea id="ev-desc" placeholder="Purpose, agenda, audience, key activities, expected outcome…">${esc(e.description || "")}</textarea>
      <div class="hint">Give your approver enough context to decide without asking follow-up questions.</div></div>
  </div>`;
}

function readEventForm() {
  return {
    event_name: val("#ev-name"), event_type: val("#ev-type"), event_date: val("#ev-date"),
    start_time: val("#ev-start"), end_time: val("#ev-end"), location: val("#ev-loc"),
    expected_attendees: val("#ev-att") || null, description: val("#ev-desc"),
    approver_ids: $$("#ev-approvers input:checked").map(i => Number(i.value)),
    miscellaneous_cost: $("#ev-misc") ? numVal("#ev-misc") : 0,
  };
}

/* Delivery is saved when the field is left, not while it is being typed into --
   a half-typed "8" on the way to "85" is not a figure anyone meant to record. */
/* "Shared" is something only the person who sent it knows. Downloading the draft sets
   it by itself; pasting a link into a chat cannot, so it is said here instead -- and can
   be taken back, because a tick made by accident should not be permanent. */
function bindShareTicks() {
  document.querySelectorAll(".share-box").forEach(box => {
    box.onchange = async () => {
      const wanted = box.checked;
      try {
        await api(`/api/events/${box.dataset.eid}/messages/${box.dataset.mid}/mark-sent`,
                  { method: "POST", body: { shared: wanted, how: "link" } });
        toast(wanted ? "Marked as shared" : "No longer marked as shared",
              wanted ? `Waiting for ${box.dataset.name} to decide.`
                     : `${box.dataset.name} is back on the list to send to.`);
        route();
      } catch (err) {
        box.checked = !wanted;
        toast("Could not save that", err.message, "bad");
      }
    };
  });
}

function bindDeliveryInputs() {
  document.querySelectorAll(".od-input").forEach(box => {
    box.onchange = async () => {
      const value = Number(box.value || 0);
      if (!(value >= 0)) { toast("Delivery cost", "It cannot be negative.", "err"); return; }
      try {
        await api(`/api/options/${box.dataset.oid}`, { method: "PUT", body: {
          name: box.dataset.oname, delivery_cost: value } });
        route();
      } catch (err) { toast("Could not save the delivery cost", err.message, "bad"); }
    };
  });
}

/* keep the picker rows visually in sync with their checkboxes.

   There are three of these now -- the event form, the history form and the record
   editor -- and a picker whose rows do not light up reads as broken, so this takes the
   selector rather than knowing about one of them. */
function bindApproverPicker(sel) {
  const box = document.querySelector(sel || "#ev-approvers");
  if (!box) return;
  box.onchange = (e) => {
    const row = e.target.closest(".picker-row");
    if (row) row.classList.toggle("on", e.target.checked);
  };
}

async function createEvent() {
  const body = readEventForm();
  if (!body.event_name) { toast("Event name is required", "Give the event a name before continuing.", "err"); $("#ev-name").focus(); return; }
  if (!body.approver_ids.length) { toast("Pick at least one approver", "Tick everyone who should receive the request.", "err"); return; }
  try {
    const d = await api("/api/events", { method: "POST", body });
    toast("Draft created", `${d.event.event_number} — now add your vendors.`);
    location.hash = `#/events/${d.event.id}/edit`;
  } catch (e) { apiError(e); }
}

/* ----------------------------------------------------------- event editor */
async function viewEdit() {
  const d = await api("/api/events/" + S.route.id);
  const e = d.event;
  if (!e.can_edit) { location.hash = `#/events/${e.id}`; return; }
  const vendorCost = e.vendor_cost || 0;
  const multi = e.options.length > 1;

  setView(`
  <div class="page-head">
    <div>
      <div class="dh-num">${esc(e.event_number)}</div>
      <h1>${esc(e.event_name)}</h1>
      <p>Step 2 of 2 — vendors, quotations and budget. ${statusBadge(e.status)}</p>
    </div>
    <div class="head-actions">
      <a class="btn btn-ghost" href="#/events/${e.id}">${icon("eye")}Preview</a>
      <button class="btn" data-act="save-event" data-id="${e.id}">${icon("check")}Save Draft</button>
      <button class="btn btn-primary" data-act="submit-event" data-id="${e.id}">${icon("send")}Submit for Approval</button>
    </div>
  </div>

  <div class="split">
    <div class="stack">
      <div class="card">
        <div class="card-head"><h3>${icon("calendar")}Event Information</h3></div>
        <div class="card-pad">${eventFormFields(e)}</div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>${icon("store")}Vendors ${multi ? `<span class="pill-count">${e.options.length} options</span>` : `<span class="pill-count">${e.vendors.length}</span>`}</h3>
          <button class="btn btn-sm" data-act="add-option" data-id="${e.id}" title="Offer the approver an alternative package">
            ${icon("plus", "icon-sm")}Add alternative option</button>
        </div>
        <div class="card-pad">
          ${multi ? `<div class="note-box mb">${icon("info", "icon-sm")} This event offers
            <b>${e.options.length} alternative options</b>. Each is a complete package with its own vendors and
            its own total — the approver picks <b>one</b>.</div>` : ""}
          ${e.options.map(o => optionEditorBlock(e, o, multi)).join("")}
        </div>
      </div>
    </div>

    <div class="stack">
      <div class="budget-panel">
        <div class="section-title" style="color:#fff;margin-bottom:10px">${icon("coin")}Budget</div>
        ${multi ? e.options.map(o => `
          <div class="bl"><span>${esc(o.name)} · ${o.vendor_count} vendor(s)</span><b>${money(o.total, false)}</b></div>`).join("")
      + `<div style="color:#9fe4ef;font-size:11.5px;margin:8px 0 0">Each option already includes the
           ${money(e.miscellaneous_cost, false)} miscellaneous cost.</div>
         <div class="grand"><span>Primary estimate (${esc(e.options[0].name)})</span><b>${money(e.total_budget, false)}</b></div>`
      : `<div class="bl"><span>Vendor costs (${e.vendors.length})</span><b>${money(vendorCost, false)}</b></div>
         <div class="bl"><span>Miscellaneous / additional</span><b>${money(e.miscellaneous_cost, false)}</b></div>
         <div class="grand"><span>Total estimated budget</span><b>${money(e.total_budget, false)}</b></div>`}
        <div style="color:#9fe4ef;font-size:11.5px;margin-top:6px">${esc(S.settings.currency)} · calculated automatically</div>
      </div>

      <div class="card card-pad">
        <div class="field hidden"><label>Miscellaneous / Additional Cost</label>
          <input type="number" min="0" step="0.01" id="ev-misc" value="${Number(e.miscellaneous_cost || 0)}">
          <div class="hint">Anything not covered by a vendor quotation (permits, printing on demand, contingency…).</div></div>
        <div class="field mt"><label>Total Estimated Budget</label>
          <input class="mono" value="${money(e.total_budget)}" readonly>
          <div class="hint">${icon("lock", "icon-sm")} Vendor totals + miscellaneous. Not editable.</div></div>
        <button class="btn btn-block mt" data-act="save-event" data-id="${e.id}">${icon("refresh")}Recalculate & Save</button>
      </div>

      <div class="card card-pad">
        <div class="section-title">${icon("send")}Submit</div>
        <p class="muted" style="font-size:12.8px;margin-top:0">Once submitted, the event is locked and sent to your
          approver by e-mail. You can withdraw it later if you need to edit.</p>
        <button class="btn btn-primary btn-block" data-act="submit-event" data-id="${e.id}">${icon("send")}Submit for Approval</button>
        <div class="divider"></div>
        <button class="btn btn-block" style="color:var(--bad);border-color:#f6cdd1" data-act="delete-event" data-id="${e.id}">
          ${icon("trash")}Delete draft</button>
      </div>
    </div>
  </div>`);

  bindApproverPicker();
  bindDeliveryInputs();
  const misc = $("#ev-misc");
  if (misc) misc.onchange = () => saveEvent(e.id, true);
}

function optionEditorBlock(e, o, multi) {
  return `
  <div class="opt-block${multi ? " multi" : ""}">
    ${multi ? `<div class="opt-head">
      <div class="opt-title">${icon("ticket", "icon-sm")}<b>${esc(o.name)}</b>
        <span class="pill-count">${o.vendor_count} vendor${o.vendor_count === 1 ? "" : "s"}</span></div>
      <div class="flex gap-sm">
        <div class="opt-total"><span>option total</span><b>${money(o.total, false)}</b></div>
        <button class="btn btn-sm" data-act="rename-option" data-oid="${o.id}"
          data-name="${esc(o.name)}" data-desc="${esc(o.description || "")}" title="Rename">${icon("pencil", "icon-sm")}</button>
        ${e.options.length > 1 ? `<button class="btn btn-sm" style="color:var(--bad)" data-act="del-option"
          data-oid="${o.id}" data-name="${esc(o.name)}" title="Remove option">${icon("trash", "icon-sm")}</button>` : ""}
      </div>
    </div>
    ${o.description ? `<div class="opt-desc">${esc(o.description)}</div>` : ""}` : ""}
    <div class="opt-body">
      ${o.vendors.length ? o.vendors.map(v => vendorEditorCard(v)).join("")
      : `<div class="empty" style="padding:26px 16px">${icon("store", "icon-lg")}
           <h4>No vendors in ${esc(o.name)}</h4>
           <p class="muted">Add at least one vendor with its quotation before submitting.</p></div>`}
      <button class="btn btn-cyan btn-sm" data-act="add-vendor" data-id="${e.id}" data-oid="${o.id}">
        ${icon("plus", "icon-sm")}Add vendor${multi ? ` to ${esc(o.name)}` : ""}</button>
      <div class="opt-foot">
        <label class="opt-delivery">Delivery cost
          <input type="number" min="0" step="0.01" class="od-input"
                 data-oid="${o.id}" data-oname="${esc(o.name)}"
                 value="${Number(o.delivery_cost || 0)}"></label>
        <span class="muted" style="font-size:12.5px">
          ${o.vendor_count} vendor(s) ${money(o.vendor_cost, false)} + delivery ${money(o.delivery_cost, false)}
          = <b>${money(o.total, false)}</b></span>
      </div>
    </div>
  </div>`;
}

function vendorEditorCard(v) {
  const files = (v.files || []).filter(f => f.kind !== "quotation");
  const images = files.filter(f => f.is_image);
  const docs = files.filter(f => !f.is_image);
  return `
  <div class="vendor-card st-${v.approval_status}">
    <div class="vc-head">
      <div class="vc-title">
        <div class="vc-ico">${icon("store")}</div>
        <div><div class="vc-name">${esc(v.vendor_name)}</div>
          <div class="vc-meta"><span class="badge b-neutral">${esc(v.category || "Uncategorised")}</span>
            ${v.contact_name ? `<span>${icon("user", "icon-sm")}${esc(v.contact_name)}</span>` : ""}</div></div>
      </div>
      <div class="flex">
        <div class="vc-amt"><div class="t">${money(v.total_amount, false)}</div>
          <div class="s">${unitLine(v)} + ${Number(v.vat_rate)}% VAT</div></div>
        <div class="row-actions">
          <button class="btn btn-sm" data-act="edit-vendor" data-vid="${v.id}" title="Edit">${icon("pencil", "icon-sm")}</button>
          <button class="btn btn-sm" style="color:var(--bad)" data-act="del-vendor" data-vid="${v.id}" title="Remove">${icon("trash", "icon-sm")}</button>
        </div>
      </div>
    </div>
    <div class="vc-body">
      ${v.description ? `<div><div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700">Scope</div>
        <div class="v" style="font-size:13.3px">${esc(v.description)}</div></div>` : ""}
      <div class="kv">
        ${v.contact_email ? `<div><div class="k">Contact e-mail</div><div class="v">${esc(v.contact_email)}</div></div>` : ""}
        ${v.contact_phone ? `<div><div class="k">Contact phone</div><div class="v mono">${esc(v.contact_phone)}</div></div>` : ""}
        <div><div class="k">Quotation</div><div class="v mono">${unitLine(v)}</div></div>
        <div><div class="k">VAT (${Number(v.vat_rate)}%)</div><div class="v mono">${money(v.vat, false)}</div></div>
      </div>
      <div>
        <div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700;margin-bottom:7px">Quotation file</div>
        ${v.quotation_file ? fileChip(v, "quotation") : `<span class="badge b-pending">${icon("alert")}No quotation uploaded</span>`}
      </div>
      ${images.length ? `<div><div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700;margin-bottom:7px">Vendor images</div>
        <div class="thumb-row">${images.map(f => `<img class="thumb" data-api-src="/api/vendor-files/${f.id}/content" alt="${esc(f.file_name)}" data-act="zoom" data-kind="Vendor file" data-src="/api/vendor-files/${f.id}/content">`).join("")}</div></div>` : ""}
      ${docs.length ? `<div class="chip-row">${docs.map(f => fileChipRow(f)).join("")}</div>` : ""}
      ${(v.links || []).length ? `<div><div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700;margin-bottom:7px">Vendor links</div>
        <div class="chip-row">${v.links.map(l => `<a class="link-chip" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">
          ${icon("link", "icon-sm")}<span>${esc(l.label || l.url)}</span>${icon("externalLink", "icon-sm")}</a>`).join("")}</div></div>` : ""}
    </div>
  </div>`;
}

function fileChip(v) {
  const fid = (v.files || []).find(f => f.kind === "quotation");
  if (!fid) return `<span class="badge b-neutral">${esc(v.quotation_name || "File")}</span>`;
  return fileChipRow(fid);
}
function fileChipRow(f) {
  return `<span class="file-chip">${icon(f.is_image ? "photo" : "fileText", "icon-sm")}
    <span class="nm">${esc(f.file_name)}</span><span class="sz">${fmtSize(f.size)}</span>
    <a href="#" data-api-href="/api/vendor-files/${f.id}/content">View</a>
    <a href="#" data-api-href="/api/vendor-files/${f.id}/content?download=1">Download</a></span>`;
}

async function saveEvent(id, silent) {
  try {
    await api("/api/events/" + id, { method: "PUT", body: readEventForm() });
    if (!silent) toast("Saved", "Your draft has been updated.");
    if (silent) await viewEdit();
    return true;
  } catch (e) { apiError(e); return false; }
}

async function submitEvent(id) {
  if ($("#ev-name")) { const ok = await api("/api/events/" + id, { method: "PUT", body: readEventForm() }).then(() => true).catch(e => { apiError(e); return false; }); if (!ok) return; }
  const yes = await confirmDialog({
    title: "Submit for approval?",
    message: "The event will be locked and an approval request will be sent to the assigned manager. You can withdraw it later if you need to make changes.",
    confirmLabel: "Submit for Approval", tone: "primary",
  });
  if (!yes) return;
  try {
    const d = await api(`/api/events/${id}/submit`, { method: "POST" });
    toast("Submitted", `${d.event.event_number} was sent to ${d.event.approver_name} for approval.`);
    location.hash = `#/events/${id}`;
    route();
  } catch (e) {
    if (e.status === 422 && e.data.missing) {
      openModal(`<div class="overlay" id="ms-ov"><div class="modal narrow">
        <div class="modal-head"><h3>Cannot submit yet</h3><button class="x-btn" id="ms-x">${icon("x")}</button></div>
        <div class="modal-body">
          <div class="warn-box">${icon("alert", "icon-sm")} The following required information is missing:</div>
          <ul class="missing-list">${e.data.missing.map(m => `<li>${esc(m)}</li>`).join("")}</ul>
        </div>
        <div class="modal-foot"><button class="btn btn-primary" id="ms-ok">Back to the form</button></div></div></div>`);
      $("#ms-x").onclick = closeModal; $("#ms-ok").onclick = closeModal;
    } else apiError(e);
  }
}

/* --------------------------------------------------------- vendor modal */
let PENDING_FILES = { quotation: null, extras: [] };

function vendorModal(eventId, vendor, optionId, options) {
  PENDING_FILES = { quotation: null, extras: [] };
  const v = vendor || {};
  const optId = optionId || v.option_id || null;
  const optName = (options || []).find(o => o.id === optId);
  const cats = S.lookups.vendor_categories;
  const vatRate = v.vat_rate !== undefined ? v.vat_rate : (S.settings.default_vat_rate ?? 15);
  const existing = (v.files || []);
  openModal(`
  <div class="overlay" id="vm-ov"><div class="modal wide">
    <div class="modal-head"><h3>${icon("store")}${vendor ? "Edit vendor" : "Add vendor"}
      ${optName ? `<span class="badge b-completed">${esc(optName.name)}</span>` : ""}</h3>
      <button class="x-btn" id="vm-x">${icon("x")}</button></div>
    <div class="modal-body">
      <div class="form-grid">
        <div class="field"><label>Vendor Name <span class="req">*</span></label>
          <input id="vn-name" value="${esc(v.vendor_name || "")}" placeholder="e.g. Riyadh Catering Co." maxlength="160"></div>
        <div class="field"><label>Vendor Category</label>
          <select id="vn-cat"><option value="">Select a category…</option>
            ${cats.map(c => `<option ${v.category === c ? "selected" : ""}>${esc(c)}</option>`).join("")}</select></div>
        <div class="field"><label>Contact Person</label>
          <input id="vn-cname" value="${esc(v.contact_name || "")}" maxlength="120"></div>
        <div class="field"><label>Contact E-mail</label>
          <input type="email" id="vn-cmail" value="${esc(v.contact_email || "")}" maxlength="160"></div>
        <div class="field"><label>Contact Phone</label>
          <input id="vn-cphone" value="${esc(v.contact_phone || "")}" placeholder="+966 5X XXX XXXX" maxlength="40"></div>
        <div class="field"><label>Price per unit <span class="req">*</span></label>
          <input type="number" min="0" step="0.01" id="vn-unit"
                 value="${v.unit_price ?? v.quotation_amount ?? ""}" placeholder="0.00"></div>
        <div class="field"><label>Quantity <span class="req">*</span></label>
          <input type="number" min="1" step="1" id="vn-qty" value="${v.quantity ?? 1}" placeholder="1">
          <div class="hint">How many. Leave at 1 for a single all-in price.</div></div>
        <div class="field"><label>Amount</label>
          <input class="mono" id="vn-amt" value="0.00" readonly>
          <div class="hint">${icon("lock", "icon-sm")} Price per unit × quantity.</div></div>
        <div class="field"><label>VAT rate (%)</label>
          <input type="number" min="0" max="100" step="0.01" id="vn-vat" value="${vatRate}"></div>
        <div class="field"><label>Vendor Total Amount</label>
          <input class="mono" id="vn-total" value="0.00" readonly>
          <div class="hint">${icon("lock", "icon-sm")} Amount + VAT — calculated automatically.</div></div>
        <div class="field full"><label>Scope / Description</label>
          <textarea id="vn-desc" style="min-height:90px" placeholder="What exactly is this vendor delivering?">${esc(v.description || "")}</textarea></div>
      </div>

      <div class="divider"></div>
      <div class="section-title">${icon("paperclip")}Quotation attachment</div>
      <div class="field">
        <input type="file" id="vn-quote" accept=".pdf,.jpg,.jpeg,.png,.doc,.docx,.xls,.xlsx">
        <div class="hint">PDF, JPG, PNG, DOC, DOCX, XLS, XLSX — up to ${limitLabel()}.
          Large pictures are resized to fit.</div>
      </div>
      ${existing.filter(f => f.kind === "quotation").map(f => `<div class="chip-row mt">${fileChipRow(f)}
        <button class="btn btn-sm" style="color:var(--bad)" data-act="del-file" data-fid="${f.id}">${icon("trash", "icon-sm")}Remove</button></div>`).join("")}

      <div class="divider"></div>
      <div class="section-title">${icon("photo")}Vendor images & extra files</div>
      <p class="muted" style="margin-top:-6px;font-size:12.8px">Portfolio photos, sample designs, company profile, menu…
        Images are shown as previews to the approver.</p>
      <div class="field"><input type="file" id="vn-extras" multiple accept=".pdf,.jpg,.jpeg,.png,.gif,.webp,.doc,.docx,.xls,.xlsx,.ppt,.pptx"></div>
      ${existing.filter(f => f.kind !== "quotation").length ? `<div class="chip-row mt">
        ${existing.filter(f => f.kind !== "quotation").map(f => `<span class="file-chip">
          ${icon(f.is_image ? "photo" : "fileText", "icon-sm")}<span class="nm">${esc(f.file_name)}</span>
          <a href="#" data-api-href="/api/vendor-files/${f.id}/content">View</a>
          <button class="btn btn-sm btn-ghost" style="color:var(--bad);padding:2px 6px" data-act="del-file" data-fid="${f.id}">${icon("x", "icon-sm")}</button>
        </span>`).join("")}</div>` : ""}

      <div class="divider"></div>
      <div class="section-title">${icon("link")}Vendor links</div>
      <p class="muted" style="margin-top:-6px;font-size:12.8px">Website, Instagram, Google Drive folder, portfolio…</p>
      <div class="toolbar" style="margin-bottom:8px">
        <input id="vn-link-label" placeholder="Label (e.g. Portfolio)" style="flex:0 0 190px;padding:9px 11px;border:1px solid var(--line-2);border-radius:10px">
        <input id="vn-link-url" placeholder="https://…" class="grow" style="padding:9px 11px;border:1px solid var(--line-2);border-radius:10px">
        <button class="btn btn-sm" id="vn-link-add">${icon("plus", "icon-sm")}Add link</button>
      </div>
      <div class="chip-row" id="vn-link-list">
        ${(v.links || []).map(l => `<span class="link-chip">${icon("link", "icon-sm")}<span>${esc(l.label || l.url)}</span>
          <button class="btn btn-sm btn-ghost" style="color:var(--bad);padding:0 4px" data-act="del-link" data-lid="${l.id}">${icon("x", "icon-sm")}</button></span>`).join("")}
      </div>
      <div id="vn-newlinks" class="chip-row mt"></div>
      <div id="vm-err" class="warn-box mt hidden"></div>
    </div>
    <div class="modal-foot">
      <button class="btn" id="vm-cancel">Cancel</button>
      <button class="btn btn-primary" id="vm-save">${icon("check")}${vendor ? "Save vendor" : "Add vendor"}</button>
    </div>
  </div></div>`);

  const newLinks = [];
  const recalc = () => {
    const unit = numVal("#vn-unit"), qty = numVal("#vn-qty") || 1, r = numVal("#vn-vat");
    const a = Math.round(unit * qty * 100) / 100;
    $("#vn-amt").value = money(a, false);
    $("#vn-total").value = money(a + (a * r / 100), false);
  };
  $("#vn-unit").oninput = recalc; $("#vn-qty").oninput = recalc;
  $("#vn-vat").oninput = recalc; recalc();

  $("#vn-quote").onchange = (ev) => { PENDING_FILES.quotation = ev.target.files[0] || null; };
  $("#vn-extras").onchange = (ev) => { PENDING_FILES.extras = Array.from(ev.target.files || []); };

  $("#vn-link-add").onclick = () => {
    const url = val("#vn-link-url"); if (!url) return;
    newLinks.push({ label: val("#vn-link-label") || null, url });
    $("#vn-link-url").value = ""; $("#vn-link-label").value = "";
    $("#vn-newlinks").innerHTML = newLinks.map((l, i) =>
      `<span class="link-chip">${icon("link", "icon-sm")}<span>${esc(l.label || l.url)}</span>
       <button class="btn btn-sm btn-ghost" style="color:var(--bad);padding:0 4px" data-newlink="${i}">${icon("x", "icon-sm")}</button></span>`).join("");
    $$("[data-newlink]").forEach(b => b.onclick = () => { newLinks.splice(+b.dataset.newlink, 1); $("#vn-link-add").onclick(); });
  };

  const close = () => closeModal();
  $("#vm-x").onclick = close; $("#vm-cancel").onclick = close;
  $("#vm-ov").onclick = (e) => { if (e.target.id === "vm-ov") close(); };

  $("#vm-save").onclick = async () => {
    const body = {
      vendor_name: val("#vn-name"), category: val("#vn-cat"), contact_name: val("#vn-cname"),
      contact_email: val("#vn-cmail"), contact_phone: val("#vn-cphone"), description: val("#vn-desc"),
      unit_price: numVal("#vn-unit"), quantity: numVal("#vn-qty") || 1,
      vat_rate: numVal("#vn-vat"),
      option_id: optId,
    };
    const err = $("#vm-err");
    if (!body.vendor_name) { err.classList.remove("hidden"); err.textContent = "Vendor name is required."; return; }
    if (!(body.unit_price > 0)) { err.classList.remove("hidden"); err.textContent = "Enter a price per unit greater than zero."; return; }
    if (!(body.quantity > 0)) { err.classList.remove("hidden"); err.textContent = "The quantity must be at least 1."; return; }
    const btn = $("#vm-save"); btn.disabled = true; btn.innerHTML = "Saving…";
    try {
      let vid = vendor ? vendor.id : null;
      if (vendor) await api("/api/vendors/" + vendor.id, { method: "PUT", body });
      else { const d = await api(`/api/events/${eventId}/vendors`, { method: "POST", body }); vid = d.vendor_id; }

      for (const l of newLinks) await api(`/api/vendors/${vid}/links`, { method: "POST", body: l });

      if (PENDING_FILES.quotation) {
        btn.innerHTML = "Uploading quotation…";
        await uploadVendorFile(vid, PENDING_FILES.quotation, "quotation");
      }
      for (let i = 0; i < PENDING_FILES.extras.length; i++) {
        btn.innerHTML = `Uploading file ${i + 1}/${PENDING_FILES.extras.length}…`;
        const f = PENDING_FILES.extras[i];
        await uploadVendorFile(vid, f, /\.(jpe?g|png|gif|webp)$/i.test(f.name) ? "image" : "attachment");
      }
      closeModal();
      toast(vendor ? "Vendor updated" : "Vendor added", body.vendor_name);
      await viewEdit();
    } catch (e) {
      btn.disabled = false; btn.innerHTML = `${icon("check")}Save vendor`;
      err.classList.remove("hidden"); err.textContent = e.message;
    }
  };
}

/* Make a picture small enough to actually arrive.

   An announcement design exported from PowerPoint or Canva is routinely 5-20 MB, and
   the hosted hub can only accept a few MB per request -- a platform ceiling we cannot
   raise. Refusing the file would be technically correct and useless: what the person
   wants is the announcement attached, not a lecture about megabytes. So scale it down
   until it fits. 2600px on the long edge is still sharper than any screen it will be
   viewed on, and the picture opens in the album viewer either way. */
const SHRINK_MAX_EDGE = 2600;

function limitLabel() {
  return (uploadCeiling() / 1024 / 1024).toFixed(1).replace(/\.0$/, "") + " MB";
}

function uploadCeiling() {
  // Until /api/auth/me has been read, assume the smaller of the two ceilings: shrinking
  // a picture that did not need it costs nothing, failing an upload costs the person.
  return S.uploadLimit || 3 * 1024 * 1024;
}

async function shrinkImage(file, budget) {
  const bitmap = await createImageBitmap(file);
  let scale = Math.min(1, SHRINK_MAX_EDGE / Math.max(bitmap.width, bitmap.height));
  for (let attempt = 0; attempt < 6; attempt++) {
    const w = Math.max(1, Math.round(bitmap.width * scale));
    const h = Math.max(1, Math.round(bitmap.height * scale));
    const canvas = document.createElement("canvas");
    canvas.width = w; canvas.height = h;
    const cx = canvas.getContext("2d");
    // A transparent PNG would go black as a JPEG, so lay it on white first.
    cx.fillStyle = "#ffffff";
    cx.fillRect(0, 0, w, h);
    cx.drawImage(bitmap, 0, 0, w, h);
    const blob = await new Promise(r => canvas.toBlob(r, "image/jpeg", 0.9));
    if (!blob) break;
    if (blob.size <= budget || attempt === 5) {
      bitmap.close && bitmap.close();
      return new File([blob], file.name.replace(/\.[^.]+$/, "") + ".jpg",
                      { type: "image/jpeg" });
    }
    scale *= 0.75;
  }
  bitmap.close && bitmap.close();
  return null;
}

const IMAGE_TYPES = /\.(jpe?g|png|gif|webp|bmp)$/i;

/* Returns { file, note } -- the file to send, and what to tell the person if it was
   not the one they picked. Throws when nothing can be done, saying what to do instead. */
async function prepareUpload(file) {
  // base64 makes the payload a third bigger again on the way out
  const budget = Math.floor(uploadCeiling() * 0.97);
  const mb = (n) => (n / 1024 / 1024).toFixed(1);
  if (file.size <= budget) return { file, note: null };

  if (!IMAGE_TYPES.test(file.name)) {
    throw new Error(`"${file.name}" is ${mb(file.size)} MB and the limit here is `
      + `${mb(budget)} MB. A PDF or slide file cannot be resized automatically — save it `
      + "smaller, or export the design as a picture and attach that.");
  }
  let smaller = null;
  try {
    smaller = await shrinkImage(file, budget);
  } catch (e) { smaller = null; }
  if (!smaller || smaller.size > budget) {
    throw new Error(`"${file.name}" is ${mb(file.size)} MB and could not be reduced `
      + `below the ${mb(budget)} MB limit. Try exporting it at a smaller size.`);
  }
  return { file: smaller,
           note: `Resized from ${mb(file.size)} MB to ${mb(smaller.size)} MB so it would fit.` };
}

function readFileB64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",")[1]);
    r.onerror = () => reject(new Error("Could not read the file."));
    r.readAsDataURL(file);
  });
}
async function uploadVendorFile(vendorId, file, kind) {
  const ready = await prepareUpload(file);
  const data = await readFileB64(ready.file);
  return api(`/api/vendors/${vendorId}/files`,
             { method: "POST", body: { kind, filename: ready.file.name, data } });
}

/* ---------------------------------------------------------- event detail */
async function viewDetail() {
  const r = await api("/api/events/" + S.route.id);
  const e = r.event;
  // every permission the server decided, so a new flag never has to be added twice
  const d = {
    can_edit: e.can_edit, can_decide: e.can_decide, can_submit: e.can_submit,
    can_complete: e.can_complete, can_upload: e.can_upload,
    can_edit_record: e.can_edit_record,
  };
  const mine = e.created_by === S.user.id || S.user.role === "admin";
  // A recap or a quarterly update is a published record, not an event with a budget.
  const isRecord = kindOf(e) !== "activity";
  const approvedVendors = e.vendors.filter(v => v.approval_status === "approved");
  const rejectedVendors = e.vendors.filter(v => v.approval_status === "rejected");
  const eventReject = (e.approvals || []).find(a => a.event_decision === "rejected");
  const eventApprove = (e.approvals || []).find(a => a.event_decision === "approved");
  const awaiting = e.status === "pending_approval" || e.status === "pending_vendor_approval";
  const actions = [
    d.can_decide ? `<a class="btn btn-cyan" href="#/events/${e.id}/approve">${icon("clipboard")}Review & decide</a>` : "",
    d.can_edit ? `<a class="btn btn-primary" href="#/events/${e.id}/edit">${icon("pencil")}Edit event</a>` : "",
    d.can_submit ? `<button class="btn btn-primary" data-act="submit-event" data-id="${e.id}">${icon("send")}Submit for approval</button>` : "",
    mine && awaiting ? `<button class="btn" data-act="withdraw" data-id="${e.id}">${icon("refresh")}Withdraw & Edit</button>` : "",
    d.can_edit_record
      ? `<button class="btn" data-act="edit-record" data-id="${e.id}">${icon("pencil")}Edit record</button>` : "",
    d.can_complete
      ? `<button class="btn btn-good" data-act="complete" data-id="${e.id}" data-date="${esc(e.event_date || "")}">${icon("flag")}Mark as executed</button>` : "",
    mine && !["cancelled", "completed", "draft"].includes(e.status)
      ? `<button class="btn" style="color:var(--bad);border-color:#f6cdd1" data-act="cancel" data-id="${e.id}">${icon("ban")}Cancel event</button>` : "",
    e.status === "fully_approved"
      ? `<span class="badge b-approved">${icon("lock")}Locked — approved events cannot be edited</span>` : "",
  ].filter(Boolean).join("");

  setView(`
  <a class="btn btn-ghost btn-sm mb" href="#/events">${icon("arrowLeft", "icon-sm")}All events</a>
  <div class="detail-head">
    <div class="dh-top">
      <div>
        <div class="dh-num">${esc(e.event_number)}</div>
        <div class="dh-title">${esc(e.event_name)}</div>
        <div class="dh-meta">
          <span>${icon("calendar", "icon-sm")}${fmtDate(e.event_date)}${e.start_time ? ` · ${esc(e.start_time)}–${esc(e.end_time || "")}` : ""}</span>
          <span>${icon("mapPin", "icon-sm")}${esc(e.location || "—")}</span>
          <span>${icon("users", "icon-sm")}${e.expected_attendees ?? "—"} attendees</span>
          <span>${icon("user", "icon-sm")}${(e.approvers || []).length > 1 ? "Approvers" : "Approver"}:
            ${esc((e.approvers || []).map(a => a.name).join(", ") || "—")}</span>
        </div>
      </div>
      <div style="text-align:right">
        ${statusBadge(e.status)}
        <div class="mt">${vendorSummaryBadge(e.vendor_summary)}</div>
      </div>
    </div>
    ${actions ? `<div class="divider"></div>
    <div class="flex" style="flex-wrap:wrap;gap:9px">${actions}</div>` : ""}
  </div>

  ${e.executed ? `<div class="exec-banner">
      <div class="ic">${icon("flag", "icon-lg")}</div>
      <div class="grow"><b>Executed on ${fmtDate(e.execution_date)}</b>
        <span>${esc(e.execution_notes || "This event took place and is filed under Completed activities.")}</span></div>
      <a class="btn btn-sm" href="#/archive">${icon("checks", "icon-sm")}Completed activities</a>
    </div>` : ""}

  ${eventApprove ? `<div class="card card-pad mb" style="border-left:4px solid var(--good)">
      <div class="section-title" style="color:var(--good)">${icon("circleCheck")}Event approved</div>
      <div class="good-box">Approved by <b>${esc(eventApprove.approver_name || "—")}</b>
        on ${fmtDateTime(eventApprove.decision_date)}.
        ${e.approvers && e.approvers.length > 1
      ? ` This request was sent to ${esc(e.approvers.map(a => a.name).join(", "))}.` : ""}</div>
    </div>` : ""}

  ${eventReject ? `<div class="card card-pad mb" style="border-left:4px solid var(--bad)">
      <div class="section-title" style="color:var(--bad)">${icon("circleX")}Event rejected</div>
      <div class="reason-box"><b>Reason:</b> ${esc(eventReject.event_rejection_reason || "—")}</div>
      <div class="faint mt" style="font-size:12px">Decided by ${esc(eventReject.approver_name || "—")} on ${fmtDateTime(eventReject.decision_date)}</div>
    </div>` : ""}

  <div class="split">
    <div class="stack">
      <div class="card">
        <div class="card-head"><h3>${icon("info")}Event Overview</h3></div>
        <div class="card-pad">
          <div class="kv" style="grid-template-columns:repeat(auto-fit,minmax(190px,1fr))">
            ${kvItem("Event ID", e.event_number, true)}
            ${kvItem("Event Type", e.event_type || "—")}
            ${kvItem("Event Date", fmtDate(e.event_date))}
            ${kvItem("Time", e.start_time ? `${esc(e.start_time)} – ${esc(e.end_time || "—")}` : "—")}
            ${isRecord ? kvItem("Audience", e.audience || "—")
              : kvItem("Location", e.location || "—")}
            ${isRecord ? "" : kvItem("Expected Attendees", e.expected_attendees ?? "—")}
            ${kvItem("Created by", e.creator_name)}
            <div style="grid-column:1/-1"><div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700">
              Approval chain</div>${chainStrip(e) || "—"}</div>
            ${isRecord ? "" : kvItem("Submitted", e.submitted_at ? fmtDateTime(e.submitted_at) : "—")}
            ${isRecord
              ? kvItem("Sharing date", e.execution_date ? fmtDate(e.execution_date) : fmtDate(e.event_date))
              : `${kvItem("Decision date", e.decided_at ? fmtDateTime(e.decided_at) : "—")}
                 ${kvItem("Executed", e.executed ? "Yes" : "Not yet")}
                 ${kvItem("Execution date", e.execution_date ? fmtDate(e.execution_date) : "—")}`}
          </div>
          <div class="divider"></div>
          <div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700">Description</div>
          <p style="margin:6px 0 0;white-space:pre-wrap">${esc(e.description || "—")}</p>
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h3>${icon("store")}Vendors
          ${e.options.length > 1 ? `<span class="pill-count">${e.options.length} options</span>` : `<span class="pill-count">${e.vendors.length}</span>`}</h3></div>
        <div class="card-pad">
          ${e.options.length > 1 ? `<div class="note-box mb">${icon("info", "icon-sm")}
            This event was submitted with <b>${e.options.length} alternative options</b>.
            ${(() => {
          const taken = e.options.filter(o => o.status === "selected");
          if (!taken.length) return "No option has been selected yet.";
          // Name who, and when more than one approver has chosen, say so: "the approver"
          // reads as a single decision where there may have been two, and an earlier
          // choice can be overturned by a later one.
          const pickers = (e.approvers || []).filter(a => a.status === "approved" && a.selected_option_name);
          const last = pickers[pickers.length - 1];
          const names = taken.map(o => `<b>${esc(o.name)}</b>`).join(" and ");
          const earlier = pickers.slice(0, -1).filter(p =>
            !taken.some(o => p.selected_option_name.includes(o.name)));
          return `${last ? `<b>${esc(last.name)}</b> selected` : "Selected:"} ${names}.${
            earlier.length ? ` Earlier, ${earlier.map(p =>
              `${esc(p.name)} had chosen ${esc(p.selected_option_name)}`).join("; ")}.` : ""}`;
        })()}</div>` : ""}
          ${e.options.map(o => {
        const chosen = e.selected_option_id === o.id;
        const decided = !!e.selected_option_id;
        return e.options.length > 1 ? `
            <div class="opt-block multi" style="${decided && !chosen ? "opacity:.6" : ""}">
              <div class="opt-head">
                <div class="opt-title">${icon("ticket", "icon-sm")}<b>${esc(o.name)}</b>
                  ${chosen ? `<span class="badge b-approved">${icon("check")}Selected</span>`
            : decided ? `<span class="badge b-cancelled">Not selected</span>`
              : `<span class="badge b-pending">${icon("clock")}Awaiting choice</span>`}</div>
                <div class="opt-total"><span>option total</span><b>${money(o.total, false)}</b></div>
              </div>
              ${o.description ? `<div class="opt-desc">${esc(o.description)}</div>` : ""}
              <div class="opt-body">${o.vendors.map(v => vendorViewCard(v)).join("")}</div>
            </div>` : (o.vendors.length ? o.vendors.map(v => vendorViewCard(v)).join("")
              : `<div class="empty">${icon("store", "icon-lg")}<h4>No vendors</h4>
                 <p class="muted">This event has no vendors yet.</p></div>`);
      }).join("")}
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>${icon("note")}Announcement shared with employees
            <span class="pill-count">${(e.announcements || []).length}</span></h3>
          ${d.can_upload ? `<label class="btn btn-sm" style="cursor:pointer;margin:0">
            ${icon("upload", "icon-sm")}Add announcement
            <input type="file" id="ann-input" accept=".jpg,.jpeg,.png,.gif,.webp,.pdf,.ppt,.pptx,.doc,.docx" multiple hidden></label>` : ""}
        </div>
        <div class="card-pad">
          ${(e.announcements || []).length ? `<div class="cd-gallery">
            ${e.announcements.map(p => `<figure>
              ${p.is_image
        ? `<img data-api-src="/api/event-photos/${p.id}/content" alt="${esc(p.file_name)}"
                     data-act="zoom" data-kind="Announcement shared with employees"
                     data-src="/api/event-photos/${p.id}/content">`
        : `<a class="ann-file" href="#" data-api-href="/api/event-photos/${p.id}/content">
                     ${icon("fileText", "icon-lg")}<span>${esc(p.file_name.split(".").pop().toUpperCase())}</span></a>`}
              <figcaption class="flex-between" style="gap:6px">
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.caption || p.file_name)}</span>
                ${d.can_upload ? `<button class="btn btn-sm btn-ghost" style="color:var(--bad);padding:0 4px"
                  data-act="del-photo" data-pid="${p.id}" title="Remove">${icon("x", "icon-sm")}</button>` : ""}
              </figcaption></figure>`).join("")}</div>`
      : `<div class="empty" style="padding:26px 16px">${icon("note", "icon-lg")}
             <h4>No announcement attached</h4>
             <p class="muted">${mine
        ? "Attach the announcement design or template you shared with employees — a picture "
          + "(JPG, PNG, GIF, WEBP), or a PDF, PPT or DOC. Large pictures are resized to fit."
        : "No announcement has been attached to this event."}</p></div>`}
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <h3>${icon("photo")}Event Pictures <span class="pill-count">${(e.photos || []).length}</span></h3>
          ${d.can_upload ? `<label class="btn btn-cyan btn-sm" style="cursor:pointer;margin:0">
            ${icon("upload", "icon-sm")}Add pictures
            <input type="file" id="ph-input" accept=".jpg,.jpeg,.png,.gif,.webp" multiple hidden></label>` : ""}
        </div>
        <div class="card-pad">
          ${(e.photos || []).length ? `<div class="cd-gallery">
            ${e.photos.map(p => `<figure>
              <img data-api-src="/api/event-photos/${p.id}/content" alt="${esc(p.file_name)}"
                   data-act="zoom" data-kind="Event picture"
                   data-src="/api/event-photos/${p.id}/content">
              <figcaption class="flex-between" style="gap:6px">
                <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(p.caption || p.file_name)}</span>
                ${d.can_upload ? `<button class="btn btn-sm btn-ghost" style="color:var(--bad);padding:0 4px"
                  data-act="del-photo" data-pid="${p.id}" title="Remove">${icon("x", "icon-sm")}</button>` : ""}
              </figcaption></figure>`).join("")}</div>`
      : `<div class="empty" style="padding:28px 16px">${icon("photo", "icon-lg")}
             <h4>No pictures yet</h4>
             <p class="muted">${mine
        ? "Add photos after the event has taken place — they show up on the completed-events calendar."
        : "No photos have been added for this event."}</p></div>`}
        </div>
      </div>

      <div class="card">
        <div class="card-head"><h3>${icon("history")}Approval Timeline</h3>
          <span class="pill-count">${e.history.length} entries</span></div>
        <div class="card-pad">${timeline(e.history)}</div>
      </div>
    </div>

    <div class="stack">
      <div class="budget-panel">
        <div class="section-title" style="color:#fff;margin-bottom:10px">${icon("coin")}Budget Summary</div>
        ${(e.options || []).map(o => `
          <div class="bl ${o.id === e.selected_option_id ? "bl-on" : ""}">
            <span>${esc(o.name)}${o.id === e.selected_option_id ? " · chosen" : ""}
              <span style="opacity:.65">— ${o.vendor_count} vendor(s) + delivery ${money(o.delivery_cost, false)}</span></span>
            <b>${money(o.total, false)}</b></div>`).join("")}
        ${["draft", "pending_approval"].includes(e.status)
          ? `<div class="grand"><span>Total</span>
               <b style="font-size:15px;opacity:.8">Not settled yet</b></div>
             <div style="color:#9fd8ea;font-size:11.5px;margin-top:4px">
               Settled by the final approval — the option totals above are estimates.</div>`
          : (() => {
              const decided = !["rejected", "cancelled"].includes(e.status);
              const taken = (e.options || []).filter(o => o.status === "selected").length;
              return `<div class="grand"><span>${decided
                ? (taken > 1 ? "Approved · both options" : "Approved")
                : "Total"}</span><b>${money(decided ? e.approved_budget : e.total_budget, false)}</b></div>
                ${decided && e.rejected_budget ? `<div style="color:#9fd8ea;font-size:11.5px;margin-top:4px">
                  ${money(e.rejected_budget, false)} was left out by the approver.</div>` : ""}`;
            })()}
        ${["draft", "pending_approval"].includes(e.status) ? "" : `
          <div class="bl" style="margin-top:12px"><span>Approved budget</span>
            <b style="color:#7ff0cd">${money(e.approved_budget, false)}</b></div>
          <div class="bl"><span>Rejected amount</span>
            <b style="color:#ffb3ba">${money(e.rejected_budget, false)}</b></div>`}
        <div style="color:#9fd8ea;font-size:11.5px;margin-top:8px">All amounts in ${esc(S.settings.currency)}</div>
      </div>

      <div class="card card-pad">
        <div class="section-title">${icon("checks")}Vendor decisions</div>
        ${(e.vendor_summary || {}).undecided ? `
          <p class="muted" style="margin:0;font-size:12.8px">Still with the approvers. What each
            vendor ends up as is settled by the <b>final approval</b> — until then an earlier
            approver's choices can be overturned, or a different option taken entirely.</p>
          <div class="bl flex-between" style="padding:7px 0;margin-top:8px"><span class="muted">Vendors in the request</span>
            <b class="mono">${e.vendor_summary.total}</b></div>`
        : `
          <div class="bl flex-between" style="padding:7px 0"><span class="muted">Approved</span>
            <b class="mono" style="color:var(--good)">${approvedVendors.length}</b></div>
          <div class="bl flex-between" style="padding:7px 0"><span class="muted">Rejected</span>
            <b class="mono" style="color:var(--bad)">${rejectedVendors.length}</b></div>
          <div class="bl flex-between" style="padding:7px 0"><span class="muted">Pending</span>
            <b class="mono" style="color:var(--warn)">${e.vendor_summary.pending}</b></div>`}
      </div>

      <div id="send-panel"></div>

      ${e.status === "pending_approval" || e.status === "pending_vendor_approval" ? `
      <div class="card card-pad">
        ${(() => {
      const at = (e.approvers || []).filter(a => (a.level || 1) === e.current_level);
      const rest = [...new Set((e.approvers || []).map(a => a.level || 1))].filter(l => l > (e.current_level || 0)).length;
      return `<div class="warn-box">${icon("hourglass", "icon-sm")} With the
        <b>${esc(ordinal(e.current_level || 1))} approver</b>
        (${esc(at.map(a => a.name).join(" or ") || "—")}) since ${fmtDateTime(e.submitted_at)}.
        ${rest ? `${rest} further level${rest > 1 ? "s" : ""} to follow once they approve.` : "This is the final approval."}</div>`;
    })()}
      </div>` : ""}
    </div>
  </div>`);

  bindDetailExtras(e);
}

/* ---- hand the composed message to the user's own mail client -------------
   mailto: bodies are plain text and browsers cap the URL length, so we send a
   trimmed plain-text version and offer "Copy formatted" for the branded HTML. */
const MAILTO_LIMIT = 1800;

function mailtoHref(m) {
  let body = m.body_text || "";
  if (body.length > MAILTO_LIMIT) {
    const cut = body.lastIndexOf("\n", MAILTO_LIMIT);
    body = body.slice(0, cut > 600 ? cut : MAILTO_LIMIT) +
      "\n\n… full details, vendors and quotations are in the approval page linked above.";
  }
  return `mailto:${encodeURIComponent(m.to_email)}?subject=${encodeURIComponent(m.subject)}`
    + `&body=${encodeURIComponent(body.replace(/\n/g, "\r\n"))}`;
}

/* The async Clipboard API only exists on secure origins (https or localhost).
   Over http://<lan-ip> it is undefined, so fall back to a selection + execCommand,
   which still carries rich formatting into Outlook. */
async function copyRich(html, text) {
  if (window.isSecureContext && navigator.clipboard && navigator.clipboard.write && window.ClipboardItem) {
    try {
      await navigator.clipboard.write([new ClipboardItem({
        "text/html": new Blob([html], { type: "text/html" }),
        "text/plain": new Blob([text || ""], { type: "text/plain" }),
      })]);
      return "rich";
    } catch (e) { /* fall through */ }
  }
  const holder = document.createElement("div");
  holder.setAttribute("contenteditable", "true");
  holder.style.cssText = "position:fixed;left:-9999px;top:0;white-space:normal;opacity:0";
  holder.innerHTML = html;
  document.body.appendChild(holder);
  try {
    const range = document.createRange();
    range.selectNodeContents(holder);
    const sel = window.getSelection();
    sel.removeAllRanges(); sel.addRange(range);
    const ok = document.execCommand("copy");
    sel.removeAllRanges();
    if (ok) return "rich";
  } catch (e) { /* fall through */ }
  finally { holder.remove(); }
  return copyPlain(text || html.replace(/<[^>]+>/g, ""));
}

function copyPlain(text) {
  if (window.isSecureContext && navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).catch(() => { });
    return "plain";
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.style.cssText = "position:fixed;left:-9999px;top:0";
  document.body.appendChild(ta);
  ta.select(); ta.setSelectionRange(0, ta.value.length);
  let ok = false;
  try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
  ta.remove();
  return ok ? "plain" : null;
}

async function copyFormatted(mailId) {
  try {
    const d = await api("/api/admin/emails/" + mailId);
    const mode = await copyRich(d.email.body_html, d.email.body_text || "");
    if (mode === "rich") toast("Copied", "Paste into Outlook — the KABi formatting is preserved.");
    else if (mode === "plain") toast("Copied as text", "Rich copy isn't available here; the plain version was copied.");
    else toast("Copy blocked", "Your browser refused the copy. Open the preview and select the text manually.", "err");
  } catch (e) { toast("Copy failed", e.message, "err"); }
}

async function renderSendPanel(e) {
  const host = $("#send-panel");
  if (!host) return;
  const mine = e.created_by === S.user.id || S.user.role === "admin";
  if (!mine || S.scoped) return;
  let d, linkData = { links: [] };
  try { d = await api(`/api/events/${e.id}/messages`); } catch (err) { return; }
  try { linkData = await api(`/api/events/${e.id}/review-links`); } catch (err) { /* none */ }
  if (d.smtp_enabled) return;

  const links = linkData.links || [];
  const onRows = new Set(links.map(l => l.message_id).filter(Boolean));
  // Anything not tied to an approver's row: a decision notification, a request at
  // another level. Listed separately because it is genuinely a different thing.
  const other = d.messages.filter(m => m.status === "queued" && !onRows.has(m.id));
  const toSend = links.filter(l => !l.shared);
  const sent = links.filter(l => l.shared);
  S.sendMessages = d.messages;

  // Once it has gone out there is nothing to do here, and the panel gets out of the way:
  // the box below already says who the event is with and since when. A single line stays,
  // because a tick made by mistake has to be undoable and the link has to be findable
  // again if the first copy went astray.
  if (!toSend.length && !other.length) {
    host.innerHTML = sent.length ? `
      <div class="card card-pad sent-line">
        ${sent.map(l => `<div class="flex-between" style="gap:10px;flex-wrap:wrap">
          <span>${icon("check", "icon-sm")} Sent to <b>${esc(l.name)}</b>${
            l.shared_at ? ` on ${fmtDateTime(l.shared_at)}` : ""}</span>
          <button class="btn btn-sm btn-ghost" data-act="unshare" data-eid="${e.id}"
                  data-mid="${l.message_id}" data-name="${esc(l.name)}">
            ${icon("refresh", "icon-sm")}Send it again</button>
        </div>`).join("")}
      </div>` : "";
    return;
  }

  host.innerHTML = `
  <div class="card card-pad">
    <div class="section-title">${icon("mail")}Send this to ${
      toSend.length === 1 ? esc(toSend[0].name) : "whoever decides next"}</div>
    <p class="muted" style="margin-top:0;font-size:12.8px">Automatic delivery is off, so the request is
      waiting here. <b>Open designed e-mail</b> downloads a ready-made Outlook draft with the full KABi
      layout — open it and press <b>Send</b>. Or paste the link straight into Teams or WhatsApp: it opens
      the approval page for that person only, <b>no password needed</b>.</p>

    ${toSend.map(l => `
      <div class="send-row">
        <div class="grow">
          <b>${esc(l.name)}</b>${l.is_final ? ` <span class="badge b-approved" style="font-size:10px;padding:1px 7px">final</span>` : ""}
          <div class="faint" style="font-size:11.5px">${esc(l.email)} · ${esc(linkData.ordinal || "")} approver</div>
          <input readonly class="link-box" value="${esc(l.url)}">
        </div>
        <div class="chip-row">
          ${l.message_id ? `
            <button class="btn btn-sm btn-primary" data-act="open-eml" data-eid="${e.id}"
                    data-mid="${l.message_id}" data-name="${esc(l.subject || "approval")}">
              ${icon("mail", "icon-sm")}Open designed e-mail</button>
            <button class="btn btn-sm" data-act="copy-html" data-mid="${l.message_id}">
              ${icon("paperclip", "icon-sm")}Copy formatted</button>`
          : `<button class="btn btn-sm" data-act="rebuild-msg" data-eid="${e.id}">
              ${icon("refresh", "icon-sm")}Prepare the e-mail</button>`}
          <button class="btn btn-sm" data-act="copy-link" data-url="${esc(l.url)}"
                  data-name="${esc(l.name)}">${icon("link", "icon-sm")}Copy link</button>
          <button class="btn btn-sm btn-ghost" data-act="rebuild-msg" data-eid="${e.id}"
                  title="Rebuild the message from the event as it stands now">
            ${icon("refresh", "icon-sm")}Refresh</button>
        </div>
        ${l.message_id ? `
          <label class="shared-tick">
            <input type="checkbox" class="share-box" data-eid="${e.id}"
                   data-mid="${l.message_id}" data-name="${esc(l.name)}">
            <span>I have sent this — mark it as waiting for approval</span>
          </label>` : ""}
      </div>`).join("")}

    ${other.length ? `<div class="divider"></div>
      <div class="section-title">${icon("mail")}Other messages waiting</div>` : ""}
    ${other.map(m => `
      <div class="send-row">
        <div class="grow">
          <b>${esc(m.to_name || m.to_email)}</b>
          <div class="faint" style="font-size:11.5px">${esc(m.to_email)} · ${esc(m.subject)}</div>
        </div>
        <div class="chip-row">
          <button class="btn btn-sm btn-primary" data-act="open-eml"
                  data-eid="${e.id}" data-mid="${m.id}" data-name="${esc(m.subject)}">
            ${icon("mail", "icon-sm")}Open designed e-mail</button>
          <button class="btn btn-sm" data-act="copy-html" data-mid="${m.id}">
            ${icon("paperclip", "icon-sm")}Copy formatted</button>
          <a class="btn btn-sm btn-ghost" href="${mailtoHref(m)}" title="Plain-text version">
            ${icon("send", "icon-sm")}Plain text</a>
        </div>
      </div>`).join("")}

    <div class="hint" style="margin-top:10px">${icon("info", "icon-sm")}
      If the downloaded <b>.eml</b> opens read-only instead of as a draft, use <b>Copy formatted</b>
      and paste straight into a new Outlook message — the design is preserved either way.</div>
  </div>`;
  bindShareTicks();
}

/* "40 x 8.60 = 344.00" when there is a quantity worth saying, otherwise just the sum.
   Printing "1 x 345.00" for a single all-in price would be noise. */
function unitLine(v) {
  const qty = Number(v.quantity || 1);
  const unit = Number(v.unit_price ?? v.quotation_amount ?? 0);
  if (qty > 1) return `${qty} × ${money(unit, false)} = ${money(v.quotation_amount, false)}`;
  return money(v.quotation_amount, false);
}

function kvItem(k, v, mono) {
  return `<div><div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700">${esc(k)}</div>
    <div class="v ${mono ? "mono" : ""}" style="font-size:13.4px;margin-top:2px">${esc(v)}</div></div>`;
}

function bindDetailExtras(e) {
  renderSendPanel(e);
  const upload = (sel, kind, label) => {
    const input = $(sel);
    if (!input) return;
    input.onchange = async (ev) => {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      let done = 0, resized = 0;
      for (const f of files) {
        try {
          const { file, note } = await prepareUpload(f);
          if (note) resized++;
          const data = await readFileB64(file);
          await api(`/api/events/${e.id}/photos`, {
            method: "POST",
            // The caption keeps the name of what they picked, even when a resized copy
            // is what actually travelled.
            body: { kind, filename: file.name, data,
                    caption: f.name.replace(/\.[^.]+$/, "") },
          });
          done++;
        } catch (err) { toast("Could not attach it", err.message, "err"); }
      }
      if (done) {
        toast(`${label} added`, `${done} file${done > 1 ? "s" : ""} uploaded.`
          + (resized ? ` ${resized} resized to fit.` : ""));
        route();
      }
    };
  };
  upload("#ph-input", "photo", "Pictures");
  upload("#ann-input", "announcement", "Announcement");
}

function vendorViewCard(v) {
  const files = v.files || [];
  const images = files.filter(f => f.is_image && f.kind !== "quotation");
  const docs = files.filter(f => !f.is_image && f.kind !== "quotation");
  const quote = files.find(f => f.kind === "quotation");
  return `
  <div class="vendor-card st-${v.approval_status}">
    <div class="vc-head">
      <div class="vc-title"><div class="vc-ico">${icon("store")}</div>
        <div><div class="vc-name">${esc(v.vendor_name)}</div>
          <div class="vc-meta"><span class="badge b-neutral">${esc(v.category || "Uncategorised")}</span>
            ${v.contact_name ? `<span>${icon("user", "icon-sm")}${esc(v.contact_name)}</span>` : ""}
            ${v.contact_email ? `<span>${icon("at", "icon-sm")}${esc(v.contact_email)}</span>` : ""}
            ${v.contact_phone ? `<span>${icon("phone", "icon-sm")}${esc(v.contact_phone)}</span>` : ""}</div></div>
      </div>
      <div style="text-align:right">
        <div class="vc-amt"><div class="t">${money(v.total_amount, false)}</div>
          <div class="s">${unitLine(v)} + VAT ${money(v.vat, false)}</div></div>
        <div class="mt">${vendorBadge(v.approval_status)}</div>
        ${decidedBy(v.decided_by, v.decided_at, v.approval_status)}
      </div>
    </div>
    <div class="vc-body">
      ${v.description ? `<div class="muted" style="font-size:13.2px">${esc(v.description)}</div>` : ""}
      <div class="chip-row">
        ${quote ? fileChipRow(quote) : `<span class="badge b-neutral">${icon("alert", "icon-sm")}No quotation file</span>`}
        ${docs.map(f => fileChipRow(f)).join("")}
      </div>
      ${images.length ? `<div class="thumb-row">${images.map(f =>
    `<img class="thumb" data-api-src="/api/vendor-files/${f.id}/content" alt="${esc(f.file_name)}" data-act="zoom" data-kind="Vendor file" data-src="/api/vendor-files/${f.id}/content">`).join("")}</div>` : ""}
      ${(v.links || []).length ? `<div class="chip-row">${v.links.map(l =>
      `<a class="link-chip" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${icon("link", "icon-sm")}
        <span>${esc(l.label || l.url)}</span>${icon("externalLink", "icon-sm")}</a>`).join("")}</div>` : ""}
      ${v.approval_status === "rejected" && v.rejection_reason
      ? `<div class="reason-box"><b>Rejection reason:</b> ${esc(v.rejection_reason)}</div>` : ""}
    </div>
  </div>`;
}

function timeline(history) {
  if (!history.length) return `<p class="muted">No history yet.</p>`;
  const tone = (h) => {
    const a = (h.action || "").toLowerCase();
    if (a.includes("reject") || a.includes("cancel")) return "bad";
    if (a.includes("approv")) return "good";
    if (a.includes("submit")) return "info";
    if (a.includes("withdraw")) return "warn";
    return "";
  };
  const ic = (h) => {
    const a = (h.action || "").toLowerCase();
    if (a.includes("reject") || a.includes("cancel")) return "x";
    if (a.includes("approv")) return "check";
    if (a.includes("submit")) return "send";
    return "check";
  };
  return `<div class="timeline">${history.map(h => `
    <div class="tl-item">
      <div class="tl-dot ${tone(h)}">${icon(ic(h))}</div>
      <div class="tl-act">${esc(h.action)}</div>
      <div class="tl-meta">${esc(h.performer_name || "System")} · ${esc((h.role || "").replace("_", " "))} · ${fmtDateTime(h.created_at)}
        ${h.previous_status && h.new_status && h.previous_status !== h.new_status
      ? ` · ${esc((STATUS[h.previous_status] || {}).label || h.previous_status)} → ${esc((STATUS[h.new_status] || {}).label || h.new_status)}` : ""}</div>
      ${h.comments ? `<div class="tl-cmt">${esc(h.comments)}</div>` : ""}
    </div>`).join("")}</div>`;
}

/* ------------------------------------------------------- manager queue */
async function viewApprovals() {
  const d = await api("/api/events?assigned_to_me=1");
  const all = await api("/api/events");
  const decided = all.events.filter(e => isMyApproval(e) &&
    ["fully_approved", "partially_approved", "rejected", "completed"].includes(e.status));
  setView(`
  <div class="page-head">
    <div><h1>Approvals</h1><p>Requests assigned to you for review.</p></div>
  </div>
  <div class="card mb">
    <div class="card-head"><h3>${icon("hourglass")}Awaiting your decision <span class="pill-count">${d.events.length}</span></h3></div>
    ${d.events.length ? `<div class="card-pad" style="display:grid;gap:14px">
      ${d.events.map(e => `
        <div class="vendor-card st-pending" style="margin:0">
          <div class="vc-head">
            <div class="vc-title"><div class="vc-ico">${icon("calendar")}</div>
              <div><div class="vc-name">${esc(e.event_name)}</div>
                <div class="vc-meta"><span class="mono">${esc(e.event_number)}</span>
                  <span>${icon("calendar", "icon-sm")}${fmtDate(e.event_date)}</span>
                  <span>${icon("mapPin", "icon-sm")}${esc(e.location || "—")}</span>
                  <span>${icon("store", "icon-sm")}${e.vendor_summary.total} vendor(s)</span>
                  <span>${icon("user", "icon-sm")}${esc(e.creator_name)}</span></div></div></div>
            <div class="flex">
              <div class="vc-amt"><div class="t">${money(e.total_budget, false)}</div>
                <div class="s">submitted ${fmtDate(e.submitted_at)}</div></div>
              <a class="btn btn-cyan" href="#/events/${e.id}/approve">${icon("clipboard")}Review Event</a>
            </div>
          </div>
        </div>`).join("")}
      </div>` : `<div class="empty">${icon("circleCheck", "icon-lg")}<h4>You're all caught up</h4>
        <p class="muted">No approval requests are waiting for you right now.</p></div>`}
  </div>

  <div class="card">
    <div class="card-head"><h3>${icon("history")}Decisions you made <span class="pill-count">${decided.length}</span></h3></div>
    ${decided.length ? eventsTable(decided) : `<div class="empty">${icon("history", "icon-lg")}<h4>No decisions yet</h4>
      <p class="muted">Events you approve or reject will be listed here.</p></div>`}
  </div>`);
}

/* ------------------------------------------------------- approval page */
async function viewApprove() {
  const d = await api("/api/events/" + S.route.id);
  const e = d.event;
  if (!e.can_decide) {
    setView(`<div class="card card-pad"><div class="empty">${icon("lock", "icon-lg")}
      <h4>This approval page is not available to you</h4>
      <p class="muted">Either you are not the assigned approver, or a decision has already been recorded.</p>
      <a class="btn mt" href="#/events/${e.id}">${icon("eye")}View the event instead</a></div></div>`);
    return;
  }
  if (!S.decision || S.decision.eventId !== e.id) {
    // Start on whatever the approver before them chose. Agreeing is the common case and
    // should not cost a click, and the card says whose choice it is -- so this is a
    // starting point that can be moved, not a decision made on their behalf.
    const upstream = [...(e.approvers || [])]
      .filter(a => a.status === "approved" && a.selected_option_id)
      .sort((x, y) => (x.level || 1) - (y.level || 1))
      .pop();
    // A set, because an event can genuinely run two packages and approving both means
    // doing both. One option is the ordinary case and still one tap.
    const fromUpstream = upstream
      ? String(upstream.selected_option_ids || upstream.selected_option_id || "")
          .split(",").map(Number).filter(id => e.options.some(o => o.id === id))
      : [];
    S.decision = {
      eventId: e.id, event: null, reason: "",
      options: e.options.length === 1 ? [e.options[0].id] : fromUpstream,
      vendors: Object.fromEntries(e.options.flatMap(o => o.vendors).map(v => [v.id,
      { decision: v.approval_status === "pending" ? null : v.approval_status, reason: v.rejection_reason || "" }])),
    };
  }
  renderApprovePage(e);
}

/* Everything the manager is about to commit, recomputed on every interaction. */
function decisionTotals(e) {
  const D = S.decision;
  const picked = e.options.filter(o => (D.options || []).includes(o.id));
  const opt = picked[0] || null;                 // the first, for anything naming one
  const vendors = picked.flatMap(o => o.vendors);
  const sum = (f) => round2(vendors.filter(f).reduce((s, v) => s + Number(v.total_amount || 0), 0));
  const dec = (id) => (D.vendors[id] || {}).decision;
  const approved = sum(v => dec(v.id) === "approved");
  const rejected = sum(v => dec(v.id) === "rejected");
  const undecided = sum(v => !dec(v.id));
  // Delivery is per option, so approving two packages carries both deliveries.
  const misc = round2(picked.reduce((t, o) => t + Number(o.delivery_cost || 0), 0));
  const estimate = round2(picked.reduce((t, o) => t + Number(o.total || 0), 0));
  return {
    opt, picked, vendors, estimate, misc, approved, rejected, undecided,
    // nothing is payable until an option is chosen, and a rejected event zeroes it out
    payable: (!picked.length || D.event === "rejected") ? 0 : round2(approved + misc),
    decided: vendors.filter(v => dec(v.id)).length,
    total: vendors.length,
    approvedCount: vendors.filter(v => dec(v.id) === "approved").length,
    rejectedCount: vendors.filter(v => dec(v.id) === "rejected").length,
  };
}
function round2(n) { return Math.round((Number(n) || 0) * 100) / 100; }

/* Who has already picked this package, if anyone. */
function choosersOf(e, optionId) {
  return (e.approvers || []).filter(
    a => a.status === "approved" && a.selected_option_id === optionId);
}

function renderApprovePage(e) {
  const D = S.decision;
  const T = decisionTotals(e);
  const multi = e.options.length > 1;
  const decidedSoFar = (e.approvers || [])
    .filter(a => a.status === "approved" && a.selected_option_name)
    .sort((x, y) => (x.level || 1) - (y.level || 1));
  const decided = T.decided, total = T.total;
  const ready = D.event === "rejected"
    ? !!D.reason.trim()
    : (D.event === "approved" && (D.options || []).length > 0);

  setView(`
  ${S.scoped ? `<div class="good-box mb">${icon("lock", "icon-sm")}
      You opened this from the approval e-mail as <b>${esc(S.user.name)}</b> — no password needed.
      Your decision is recorded against this name and e-mail.</div>`
      : `<a class="btn btn-ghost btn-sm mb" href="#/approvals">${icon("arrowLeft", "icon-sm")}Back to approvals</a>`}

  <div class="detail-head">
    <div class="dh-top">
      <div>
        <div class="dh-num">${esc(e.event_number)}</div>
        <div class="dh-title">${esc(e.event_name)}</div>
        <div class="dh-meta">
          <span>${icon("calendar", "icon-sm")}${fmtDate(e.event_date)}${e.start_time ? ` · ${esc(e.start_time)}–${esc(e.end_time || "")}` : ""}</span>
          <span>${icon("mapPin", "icon-sm")}${esc(e.location || "—")}</span>
          <span>${icon("users", "icon-sm")}${e.expected_attendees ?? "—"} attendees</span>
          <span>${icon("ticket", "icon-sm")}${esc(e.event_type || "—")}</span>
          <span>${icon("user", "icon-sm")}Requested by ${esc(e.creator_name)}</span>
        </div>
      </div>
      <div style="text-align:right">
        <div class="faint" style="font-size:11.5px;text-transform:uppercase;letter-spacing:.7px;font-weight:700">
          ${multi ? (T.opt ? `Estimated · ${esc(T.opt.name)}` : `${e.options.length} options · from ${money(Math.min(...e.options.map(o => o.total)), false)}`) : "Total estimated budget"}</div>
        <div class="mono" style="font-size:27px;font-weight:800;color:var(--navy)">
          ${T.opt ? money(T.estimate, false) : "—"}</div>
        <div class="faint" style="font-size:12px">${esc(S.settings.currency)}${T.opt ? ` · ${total} vendor(s) + misc.` : " · choose an option below"}</div>
      </div>
    </div>
    <div class="divider"></div>
    <div><div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700">Description</div>
      <p style="margin:6px 0 0;white-space:pre-wrap">${esc(e.description || "—")}</p></div>
    ${(e.approvers || []).length > 1 ? `<div class="divider"></div>
      <div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700">Approval chain</div>
      ${chainStrip(e)}` : ""}
  </div>

  ${multi ? `<div class="card mb">
    <div class="card-head"><h3>${icon("ticket")}Choose an option <span class="pill-count">${e.options.length} alternatives</span></h3>
      ${(D.options || []).length ? `<span class="badge b-completed">${icon("check")}${
        esc(T.picked.map(o => o.name).join(" + "))} selected</span>` : ""}</div>
    <div class="card-pad">
      <p class="muted" style="margin-top:0">Each option is a complete package with its own vendors,
        its own delivery and its own total. Tick everything you are approving — <b>more than one
        is allowed</b>, and then both are happening and both are paid for. Anything left unticked
        is recorded as <b>not selected</b> and costs nothing.</p>
      ${decidedSoFar.length ? `<div class="info-box mb">${icon("info", "icon-sm")}
        <span>${decidedSoFar.map(a => `<b>${esc(a.name)}</b> (${esc(ordinal(a.level || 1))} approver)
          chose <b>${esc(a.selected_option_name)}</b>`).join("; ")}.
          You can approve the same package or a different one — <b>your choice is the one
          that stands</b>.</span></div>` : ""}
      <div class="opt-grid">
        ${e.options.map(o => `
          <div class="opt-card ${(D.options || []).includes(o.id) ? "on" : ""}"
               data-act="pick-option" data-oid="${o.id}">
            <div class="opt-radio">${(D.options || []).includes(o.id) ? icon("check") : ""}</div>
            <div class="grow">
              <div class="n">${esc(o.name)}</div>
              ${choosersOf(e, o.id).map(a => `<div class="opt-chose">${icon("check", "icon-sm")}
                ${esc(a.name)} chose this</div>`).join("")}
              ${o.description ? `<div class="m">${esc(o.description)}</div>` : ""}
              <div class="t">${money(o.total, false)}</div>
              <div class="m">${o.vendor_count} vendor(s) ${money(o.vendor_cost, false)} + misc ${money(e.miscellaneous_cost, false)}</div>
              <ul class="vl">${o.vendors.map(v => `<li>${esc(v.vendor_name)} — ${money(v.total_amount, false)}</li>`).join("")}</ul>
            </div>
          </div>`).join("")}
      </div>
    </div>
  </div>` : ""}

  <div class="card mb">
    <div class="card-head"><h3>${icon("store")}Choose the vendors you approve
      ${T.picked.length && multi ? `<span class="badge b-completed">${
        esc(T.picked.map(o => o.name).join(" + "))}</span>` : ""}
      <span class="pill-count">${T.approvedCount}/${total} included</span></h3></div>
    <div class="card-pad">
      ${!T.picked.length ? `<div class="warn-box">${icon("alert", "icon-sm")}
        Tick one or more of the options above to review their vendors.</div>`
      : `${!T.approvedCount ? `<div class="note-box mb">${icon("info", "icon-sm")}
          Tap each vendor you want to include. Anything you leave out is recorded as
          <b>not selected</b> and costs nothing. Then press <b>Approve</b> below.</div>`
        : `<div class="live-bar">
            <div class="live-cell"><div class="k">Estimated (${esc(T.picked.map(o => o.name).join(" + "))})</div>
              <div class="v">${money(T.estimate, false)}</div></div>
            <div class="live-cell approved"><div class="k">Included (${T.approvedCount})</div>
              <div class="v">${money(T.approved, false)}</div></div>
            ${T.undecided ? `<div class="live-cell rejected"><div class="k">Not selected</div>
              <div class="v">−${money(T.undecided, false)}</div></div>` : ""}
            <div class="live-cell final"><div class="k">Approved budget</div>
              <div class="v">${money(T.payable, false)}</div></div>
          </div>`}

        ${T.picked.map(o => `${multi ? `<div class="opt-split">${esc(o.name)}</div>` : ""}
          ${o.vendors.map(v => approvalVendorCard(v, D.vendors[v.id])).join("")}`).join("")}
        ${total === 0 ? `<div class="empty">${icon("store", "icon-lg")}<h4>No vendors in this option</h4></div>` : ""}`}
    </div>
    <div class="sticky-foot">
      <div class="muted" style="font-size:12.8px">
        ${icon("info", "icon-sm")}
        ${multi && T.opt ? `<b>${esc(T.opt.name)}</b> · ` : ""}
        <b>${T.approvedCount}</b> of ${total} vendor${total === 1 ? "" : "s"} included
        · Approved budget <b class="mono">${money(T.payable)}</b>
        ${T.approvedCount ? "" : " · include at least one vendor to approve"}
      </div>
      <div class="chip-row">
        <button class="btn btn-bad" data-act="reject-all">${icon("circleX")}Reject request</button>
        <button class="btn btn-good" data-act="submit-decision" ${T.approvedCount ? "" : "disabled"}>
          ${icon("circleCheck")}Approve${T.approvedCount ? ` (${T.approvedCount})` : ""}</button>
      </div>
    </div>
  </div>`);

  const ta = $("#dec-reason");
  if (ta) { ta.oninput = () => { S.decision.reason = ta.value; }; }
  $$("[data-vreason]").forEach(t => { t.oninput = () => { S.decision.vendors[t.dataset.vreason].reason = t.value; }; });
}

function approvalVendorCard(v, dec) {
  const files = v.files || [];
  const images = files.filter(f => f.is_image && f.kind !== "quotation");
  const docs = files.filter(f => !f.is_image && f.kind !== "quotation");
  const quote = files.find(f => f.kind === "quotation");
  const chosen = dec.decision === "approved";
  const st = chosen ? "approved" : "not_selected";
  return `
  <div class="vendor-card pick ${chosen ? "on" : ""} st-${st}" data-act="vn-pick" data-vid="${v.id}">
    <div class="vc-head">
      <div class="vc-title">
        <span class="pick-box">${chosen ? icon("check") : ""}</span>
        <div class="vc-ico">${icon("store")}</div>
        <div><div class="vc-name">${esc(v.vendor_name)}</div>
          <div class="vc-meta"><span class="badge b-neutral">${esc(v.category || "Uncategorised")}</span>
            ${v.contact_name ? `<span>${icon("user", "icon-sm")}${esc(v.contact_name)}</span>` : ""}
            ${v.contact_email ? `<span>${icon("at", "icon-sm")}${esc(v.contact_email)}</span>` : ""}
            ${v.contact_phone ? `<span>${icon("phone", "icon-sm")}${esc(v.contact_phone)}</span>` : ""}</div></div></div>
      <div style="text-align:right">
        <div class="vc-amt"><div class="t">${money(v.total_amount, false)}</div>
          <div class="s">${unitLine(v)} + VAT ${money(v.vat, false)} (${Number(v.vat_rate)}%)</div></div>
        <div class="mt">${chosen
      ? `<span class="badge b-approved">${icon("check")}Included</span>`
      : `<span class="badge b-neutral">${icon("plus")}Tap to include</span>`}</div>
      </div>
    </div>
    <div class="vc-body">
      ${v.description ? `<div><div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700">Scope</div>
        <div style="font-size:13.3px;margin-top:3px">${esc(v.description)}</div></div>` : ""}
      <div class="chip-row">
        ${quote ? fileChipRow(quote) : `<span class="badge b-pending">${icon("alert", "icon-sm")}No quotation file attached</span>`}
        ${docs.map(f => fileChipRow(f)).join("")}
      </div>
      ${images.length ? `<div><div class="k" style="font-size:11px;text-transform:uppercase;letter-spacing:.6px;color:var(--faint);font-weight:700;margin-bottom:7px">Vendor images</div>
        <div class="thumb-row">${images.map(f => `<img class="thumb" data-api-src="/api/vendor-files/${f.id}/content"
          alt="${esc(f.file_name)}" data-act="zoom" data-kind="Vendor file" data-src="/api/vendor-files/${f.id}/content">`).join("")}</div></div>` : ""}
      ${(v.links || []).length ? `<div class="chip-row">${v.links.map(l =>
    `<a class="link-chip" href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">${icon("link", "icon-sm")}
      <span>${esc(l.label || l.url)}</span>${icon("externalLink", "icon-sm")}</a>`).join("")}</div>` : ""}
      ${dec.decision === "approved" ? "" : `<div class="field" style="margin-top:4px">
        <textarea data-vreason="${v.id}" style="min-height:56px"
          placeholder="Optional: why this vendor isn't included…">${esc(dec.reason)}</textarea></div>`}
    </div>
  </div>`;
}

async function submitDecision() {
  const D = S.decision;
  if (!D) return;
  if (D.event !== "rejected") D.event = "approved";
  if (D.event === "rejected" && !D.reason.trim()) { toast("Rejection reason required", "Enter why the request is rejected.", "err"); return; }

  const res = await api("/api/events/" + D.eventId);
  const e = res.event;
  const multi = e.options.length > 1;
  if (D.event === "approved" && multi && !(D.options || []).length) {
    toast("Choose an option", "Tick the option or options you are approving.", "err"); return;
  }
  const T = decisionTotals(e);
  const ids = new Set(T.vendors.map(v => v.id));


  const mine = (e.approvers || []).find(a => a.id === S.user.id) || {};
  const levels = [...new Set((e.approvers || []).map(a => a.level || 1))].sort((x, y) => x - y);
  const upcoming = levels.find(l => l > (mine.level || 1));
  const closes = mine.is_final || upcoming === undefined;

  let outcome;
  if (D.event === "rejected") outcome = "Rejected — the chain stops here";
  else if (!closes) outcome = `Moves to the ${ordinal(upcoming)} approver`;
  else if (T.approvedCount === T.total) outcome = "Fully Approved";
  else outcome = "Partially Approved";

  const ok = await confirmDialog({
    title: "Submit your decision?",
    message: `<b>Event:</b> ${D.event === "approved" ? "Approved" : "Rejected"}<br>
      ${multi ? `<b>${T.picked.length > 1 ? "Options" : "Option"}:</b> ${
        esc(T.picked.map(o => o.name).join(" + ")) || "—"}${
        e.options.some(o => !T.picked.includes(o))
      ? ` &nbsp;<span class="muted">(${e.options.filter(o => !T.picked.includes(o))
          .map(o => esc(o.name)).join(", ")} not selected)</span>` : ""}<br>` : ""}
      <b>Vendors:</b> ${T.approvedCount} included${T.total - T.approvedCount
        ? ` · ${T.total - T.approvedCount} not selected` : ""}<br>
      <b>Approved budget:</b> <span class="mono">${money(T.payable)}</span><br>
      <b>Result:</b> ${esc(outcome)}<br><br>
      <span class="muted">${closes || D.event === "rejected"
        ? "The requester will be notified immediately. This cannot be undone."
        : `Your approval is recorded and the request is e-mailed to the ${esc(ordinal(upcoming))} approver.`}</span>`,
    confirmLabel: "Submit Decision", tone: "primary",
  });
  if (!ok) return;

  try {
    const body = {
      event_decision: D.event, event_rejection_reason: D.reason,
      selected_option_ids: D.options,
      vendors: Object.entries(D.vendors).filter(([id]) => ids.has(Number(id)))
        .map(([id, v]) => ({ id: Number(id),
          decision: v.decision === "approved" ? "approved" : "not_selected",
          rejection_reason: v.reason })),
    };
    const d = await api(`/api/events/${D.eventId}/decision`, { method: "POST", body });
    S.decision = null;
    toast("Decision submitted", d.message);
    location.hash = `#/events/${body_id(d)}`;
    route();
  } catch (e) { apiError(e); }
}
function body_id(d) { return d.event.id; }

/* ------------------------------------------------------- calendar of the
   completed-events history: a month grid of clickable event cards. */
const MONTHS = ["January", "February", "March", "April", "May", "June",
  "July", "August", "September", "October", "November", "December"];
const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function monthKey(d) { return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0"); }

async function renderCalendar() {
  const host = $("#cal-host");
  if (!host) return;
  host.innerHTML = loadingView();
  let d;
  try { d = await api("/api/archive?kind=" + encodeURIComponent(S.recordKind)); } catch (e) { host.innerHTML = `<div class="card card-pad">${esc(e.message)}</div>`; return; }
  S.archive = d.events;

  const cur = S.calMonth ? new Date(S.calMonth + "-01T00:00:00") : new Date();
  const y = cur.getFullYear(), m = cur.getMonth();
  S.calMonth = monthKey(cur);

  const byDay = {};
  d.events.forEach(e => {
    const k = String(e.calendar_date || "").slice(0, 10);
    if (k) (byDay[k] = byDay[k] || []).push(e);
  });

  // Monday-first grid
  const first = new Date(y, m, 1);
  const lead = (first.getDay() + 6) % 7;
  const days = new Date(y, m + 1, 0).getDate();
  const cells = [];
  for (let i = 0; i < lead; i++) cells.push(null);
  for (let i = 1; i <= days; i++) cells.push(i);
  while (cells.length % 7) cells.push(null);

  const inMonth = d.events.filter(e => String(e.calendar_date || "").slice(0, 7) === S.calMonth);
  const monthSpend = inMonth.reduce((s, e) => s + Number(e.approved_budget || e.total_budget || 0), 0);
  const today = new Date().toISOString().slice(0, 10);

  host.innerHTML = `
  <div class="card mb">
    <div class="cal-head">
      <div class="flex">
        <button class="icon-btn cal-nav" data-act="cal-move" data-by="-1" title="Previous month">${icon("arrowLeft")}</button>
        <div class="cal-title"><b>${MONTHS[m]} ${y}</b>
          <span>${inMonth.length} event${inMonth.length === 1 ? "" : "s"}
            ${inMonth.length ? `· ${money(monthSpend)}` : ""}</span></div>
        <button class="icon-btn cal-nav" data-act="cal-move" data-by="1" title="Next month">${icon("arrowRight")}</button>
      </div>
      <div class="chip-row">
        <button class="btn btn-sm" data-act="cal-today">${icon("calendar", "icon-sm")}This month</button>
        <span class="badge b-approved">${icon("flag")}Executed</span>
        <span class="badge b-cancelled">${icon("ban")}Cancelled</span>
      </div>
    </div>
    <div class="cal-grid">
      ${DOW.map(x => `<div class="cal-dow">${x}</div>`).join("")}
      ${cells.map(day => {
    if (!day) return `<div class="cal-cell empty"></div>`;
    const key = `${y}-${String(m + 1).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
    const list = byDay[key] || [];
    return `<div class="cal-cell ${key === today ? "today" : ""} ${list.length ? "has" : ""}"
      data-day="${key}">
          <div class="cal-day">${day}</div>
          ${list.map(e => calBadge(e)).join("")}
        </div>`;
  }).join("")}
    </div>
  </div>
  <div class="note-box mb">${icon("info", "icon-sm")} The calendar shows events that actually ran
    (<b>executed</b>) plus <b>cancelled</b> ones. Switch to <b>List</b> for every closed record including
    rejected requests.</div>
  ${d.events.length ? "" : `<div class="card"><div class="empty">${icon("calendar", "icon-lg")}
      <h4>Nothing on the calendar yet</h4>
      <p class="muted">An event appears here once you mark it as executed — or if it was cancelled.</p></div></div>`}`;
  hydrateImages(host);   // the month arrows call this directly, bypassing route()
  wireCalendarDrag(host);
}

function calBadge(e) {
  const tone = e.executed ? "ok" : e.status === "rejected" ? "no"
    : e.status === "cancelled" ? "off" : "warn";
  const photo = (e.photos || [])[0];
  const kind = kindOf(e);
  const isRecord = kind !== "activity";
  const shots = (e.photos || []).length;
  // A recap has no budget, so printing 0.00 under it is worse than saying nothing --
  // it invites the reader to wonder what happened to the money. Say what it is instead.
  const line = isRecord
    ? `${KIND_LABEL[kind]}${shots ? ` · ${shots} page${shots > 1 ? "s" : ""}` : ""}`
    : `${money(e.approved_budget || e.total_budget, false)}${shots ? ` · ${shots} photo${shots > 1 ? "s" : ""}` : ""}`;
  const cls = kind === "monthly_recap" ? "kind-recap"
    : kind === "quarterly_update" ? "kind-quarter" : tone;

  return `<button class="cal-badge ${cls}" data-act="cal-open" data-id="${e.id}"
      draggable="${S.canMoveDates ? "true" : "false"}" data-drag-id="${e.id}"
      data-date="${esc(e.execution_date || e.event_date || "")}"
      title="${esc(e.event_name)}${isRecord ? "" : ` — ${money(e.approved_budget || e.total_budget)}`}${
        S.canMoveDates ? " · drag to another day to move it" : ""}">
    ${isRecord ? `<span class="cal-ic ${kind === "monthly_recap" ? "recap" : "quarter"}">
        ${icon(kind === "monthly_recap" ? "note" : "clipboard", "icon-sm")}</span>`
      : photo ? `<img data-api-src="${esc(photo.src)}" alt="">`
      : `<span class="cal-ic">${icon(e.executed ? "flag" : e.status === "rejected" ? "circleX" : "calendar", "icon-sm")}</span>`}
    <span class="cal-txt"><b>${esc(e.event_name)}</b>
      <span>${esc(line)}</span></span>
  </button>`;
}

/* Dragging a card onto another day moves the record's date.

   Only the people who could edit the record anyway can do it, and it goes through the
   same endpoint the edit form uses -- so there is one rule about who may change a date,
   not two. The card is put back if the server refuses. */
function wireCalendarDrag(host) {
  if (!S.canMoveDates) return;
  let carried = null;

  host.querySelectorAll("[data-drag-id]").forEach(card => {
    card.addEventListener("dragstart", (ev) => {
      carried = { id: card.dataset.dragId, from: card.dataset.date };
      card.classList.add("dragging");
      ev.dataTransfer.effectAllowed = "move";
      ev.dataTransfer.setData("text/plain", card.dataset.dragId);
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
  });

  host.querySelectorAll(".cal-cell[data-day]").forEach(cell => {
    cell.addEventListener("dragover", (ev) => {
      if (!carried) return;
      ev.preventDefault();
      cell.classList.add("drop-here");
    });
    cell.addEventListener("dragleave", () => cell.classList.remove("drop-here"));
    cell.addEventListener("drop", async (ev) => {
      ev.preventDefault();
      cell.classList.remove("drop-here");
      if (!carried) return;
      const to = cell.dataset.day;
      const { id, from } = carried;
      carried = null;
      if (!to || to === from) return;
      try {
        await api(`/api/events/${id}/record`, {
          method: "PUT", body: { event_date: to, execution_date: to },
        });
        toast("Date moved", `Now filed on ${fmtDate(to)}.`);
        renderCalendar();
      } catch (err) {
        toast("Could not move it", err.message, "bad");
        renderCalendar();
      }
    });
  });
}

/* the clickable card: full details plus the event's picture gallery */
/* Record an event that already happened.

   Everything the hub did not run still belongs in one history, so this takes the same
   details as a normal event plus the date it actually ran. Vendors and costs are
   optional: older activities frequently have no cost records, and an honest zero beats
   an invented figure. */
function addCompletedForm() {
  // shaped after the markup is on screen, at the end of this function

  const people = (S.lookups.approvers || []);
  const owners = [{ id: S.user.id, name: S.user.name, email: S.user.email }]
    .concat(people.filter(p => p.email !== S.user.email));
  formModal(`
    <h3>${icon("checks")}Add to the history</h3>
    <p class="muted" style="margin-top:0;font-size:12.8px">For something that has already
      happened or already gone out. It goes straight into the history and onto the calendar.</p>
    <div class="field"><label>What is this? <span class="req">*</span></label>
      <select id="ce-kind">
        <option value="activity">An event or activity that took place</option>
        <option value="monthly_recap">A monthly recap that was shared</option>
        <option value="quarterly_update">A quarterly update that was shared</option>
      </select>
      <div class="hint" id="ce-kind-hint"></div></div>

    <div class="grid-2" id="ce-period" style="display:none">
      <div class="field"><label>Year <span class="req">*</span></label>
        <input type="number" id="ce-year" min="2020" max="2100" step="1"></div>
      <div class="field" id="ce-month-wrap"><label>Month <span class="req">*</span></label>
        <select id="ce-month">${MONTH_NAMES.map((m, i) =>
          `<option value="${i + 1}">${m}</option>`).join("")}</select></div>
      <div class="field" id="ce-quarter-wrap" style="display:none"><label>Quarter <span class="req">*</span></label>
        <select id="ce-quarter">${[1, 2, 3, 4].map(q =>
          `<option value="${q}">Q${q}</option>`).join("")}</select></div>
      <div class="field full"><div class="hint" id="ce-when"></div></div>
    </div>

    <div class="grid-2">
      <div class="field"><label id="ce-name-label">Event name <span class="req">*</span></label>
        <input id="ce-name" placeholder="e.g. World Kindness Day 2026" required></div>
      <div class="field"><label>Event type</label>
        <select id="ce-type">${(S.lookups.event_types || []).map(t =>
          `<option>${esc(t)}</option>`).join("")}</select></div>
      <div class="field" id="ce-date-wrap"><label>Date it took place <span class="req">*</span></label>
        <input type="date" id="ce-date"></div>
      <div class="field"><label>Execution date <span class="hint-inline">defaults to the date above</span></label>
        <input type="date" id="ce-exec"></div>
      <div class="field"><label>Location</label>
        <input id="ce-loc" placeholder="e.g. Riyadh office"></div>
      <div class="field"><label>Attendees</label>
        <input type="number" id="ce-att" min="0" placeholder="e.g. 40"></div>
      <div class="field"><label>Owner</label>
        <select id="ce-owner">${owners.map(o =>
          `<option value="${esc(o.email)}">${esc(o.name)}</option>`).join("")}</select></div>
      <div class="field" id="ce-audience-wrap" style="display:none"><label>Audience</label>
        <input id="ce-audience" value="KABi MENA"></div>
      <div class="field full" id="ce-approver-wrap" style="display:none"><label>Approved by</label>
        <div class="picker" id="ce-approvers">${(S.lookups.approvers || []).map(a =>
          approverRow(a, (S.lookups.record_approvers || []).includes(a.id))).join("")}</div>
        <div class="hint">Recaps and updates are signed off by more than one person, so tick
          everyone who did. Tick another name here whenever that changes.</div></div>
      <div class="field" id="ce-misc-wrap"><label>Miscellaneous cost <span class="hint-inline">leave blank if unknown</span></label>
        <input type="number" id="ce-misc" min="0" step="0.01" placeholder="0.00"></div>
    </div>
    <div class="field"><label>Description</label>
      <textarea id="ce-desc" rows="3" placeholder="What happened, and where"></textarea></div>
    <div class="field"><label>Notes on execution</label>
      <input id="ce-notes" placeholder="e.g. Recorded from the Oct 2025 monthly recap"></div>

    <div id="ce-vendor-block">
    <div class="section-title" style="margin-top:14px">${icon("truck")}Vendors and cost
      <span class="hint-inline">optional — add only if you have the records</span></div>
    <div id="ce-vendors"></div>
    <button class="btn btn-sm" data-act="ce-add-vendor">${icon("plus", "icon-sm")}Add a vendor</button>
    </div>

    <div class="hint" style="margin-top:12px">${icon("info", "icon-sm")}
      Pictures and the announcement are attached from the event page once it is created.</div>
  `, "Add to history", async () => {
    const kind = val("#ce-kind");
    const name = $("#ce-name").value.trim();
    const date = $("#ce-date").value;
    if (!name) { toast("Missing details", "A name is required.", "bad"); return false; }
    if (kind === "activity" && !date) {
      toast("Missing details", "A date is required.", "bad"); return false;
    }
    const vendors = kind !== "activity" ? [] :
      [...document.querySelectorAll("#ce-vendors .ce-vrow")].map(row => ({
      vendor_name: row.querySelector(".ce-vname").value.trim(),
      category: row.querySelector(".ce-vcat").value.trim(),
      quotation_amount: row.querySelector(".ce-vamt").value || 0,
    })).filter(v => v.vendor_name);
    const body = {
      record_kind: kind,
      event_name: name,
      event_type: $("#ce-type").value,
      // A recap is filed by the period it covers; the hub works out the Thursday.
      period_year: Number($("#ce-year").value) || null,
      period_month: Number($("#ce-month").value) || null,
      period_quarter: Number($("#ce-quarter").value) || null,
      // For a recap the date field is hidden -- the period decides the date. Sending
      // whatever it happened to hold would override that silently, and did: a recap
      // for August 2026 was filed on Monday the 31st instead of Thursday the 27th,
      // because a date typed while "Event or activity" was selected survived the switch.
      event_date: kind === "activity" ? (date || null) : null,
      execution_date: kind === "activity" ? ($("#ce-exec").value || date || null) : null,
      location: $("#ce-loc").value.trim(),
      expected_attendees: $("#ce-att").value || null,
      description: $("#ce-desc").value.trim(),
      execution_notes: $("#ce-notes").value.trim(),
      miscellaneous_cost: $("#ce-misc").value || 0,
      creator_email: $("#ce-owner").value,
      audience: kind === "activity" ? null : ($("#ce-audience").value.trim() || null),
      approver_ids: kind === "activity" ? []
        : orderedRecordApprovers(pickedApprovers("#ce-approvers")),
      vendors: kind === "activity" ? vendors : [],
    };
    const out = await api("/api/events/completed", { method: "POST", body });
    if (out.created === false) {
      toast("Already recorded", `${out.event_number} covers this name and date.`);
    } else {
      toast("Added to history", `${out.event_number} is on the calendar as executed.`);
    }
    location.hash = `#/events/${out.event_id}`;
    return true;
  });

  ceSyncKind();
  bindApproverPicker("#ce-approvers");
  $("#ce-kind").onchange = ceSyncKind;
  ["#ce-year", "#ce-month", "#ce-quarter"].forEach(sel => {
    const el = $(sel);
    if (el) el.onchange = ceSyncKind;
  });
}

/* Shape the form to what is being recorded. A recap has no vendors and no cost, and
   its date is not a choice -- it is the last Thursday of the period it covers, which
   is when IC publishes. Showing a date picker there would invite a wrong answer. */
function ceSyncKind() {
  const kind = val("#ce-kind");
  const isActivity = kind === "activity";
  const isQuarter = kind === "quarterly_update";
  const show = (sel, on) => { const el = $(sel); if (el) el.style.display = on ? "" : "none"; };

  show("#ce-period", !isActivity);
  show("#ce-audience-wrap", !isActivity);
  show("#ce-approver-wrap", !isActivity);
  show("#ce-month-wrap", kind === "monthly_recap");
  show("#ce-quarter-wrap", isQuarter);
  show("#ce-date-wrap", isActivity);
  show("#ce-vendor-block", isActivity);
  show("#ce-misc-wrap", isActivity);

  $("#ce-name-label").innerHTML = isActivity
    ? 'Event name <span class="req">*</span>'
    : 'Title <span class="req">*</span>';
  $("#ce-kind-hint").textContent = isActivity
    ? "Filed on the day it took place."
    : "Filed on the last Thursday of the period, which is when it goes out. "
      + "No budget and no vendors — a recap is written, not bought.";

  // The people who sign these off come from the hub's own list, already ticked. Nothing
  // here has to be chosen for the common case, and any of it can be changed.

  if (!isActivity) {
    if (!$("#ce-year").value) $("#ce-year").value = new Date().getFullYear();
    const year = Number($("#ce-year").value);
    const month = isQuarter ? Number(val("#ce-quarter")) * 3 : Number(val("#ce-month"));
    const when = lastThursday(year, month);
    $("#ce-when").textContent = when
      ? `This will be filed on ${fmtDate(when)} — the last Thursday of ${MONTH_NAMES[month - 1]} ${year}.`
      : "";
    if (!$("#ce-name").value.trim()) {
      $("#ce-name").value = isQuarter
        ? `Quarterly Update — Q${val("#ce-quarter")} ${year}`
        : `Monthly Recap — ${MONTH_NAMES[month - 1].slice(0, 3)} ${year}`;
    }
  }
}

/* The same rule the server uses, so the form can say the date before it is saved. */
function lastThursday(year, month) {
  if (!year || !month) return null;
  const d = new Date(Date.UTC(year, month, 0));          // the month's last day
  d.setUTCDate(d.getUTCDate() - ((d.getUTCDay() + 3) % 7));
  return d.toISOString().slice(0, 10);
}

function ceVendorRow() {
  const box = $("#ce-vendors");
  const row = document.createElement("div");
  row.className = "ce-vrow flex";
  row.style.cssText = "gap:8px;align-items:flex-start";
  row.innerHTML = `
    <div class="field"><input class="ce-vname" placeholder="Vendor name"></div>
    <div class="field"><input class="ce-vcat" placeholder="Category"></div>
    <div class="field flex" style="gap:6px">
      <input class="ce-vamt" type="number" min="0" step="0.01" placeholder="Amount">
      <button class="icon-btn" data-act="ce-del-vendor" title="Remove">${icon("x", "icon-sm")}</button></div>`;
  box.appendChild(row);
}

/* Correct the record of a past event: dates, place, description. Budgets and approvals
   are deliberately left alone -- those must keep matching what was approved. */
function editRecordForm(e) {
  formModal(`
    <h3>${icon("pencil")}Edit this record</h3>
    <p class="muted" style="margin-top:0;font-size:12.8px">${esc(e.event_number)} — a completed
      record. Approved figures and vendor decisions are not changed here.</p>
    <div class="grid-2">
      <div class="field"><label>Event name <span class="req">*</span></label>
        <input id="er-name" value="${esc(e.event_name || "")}" required></div>
      <div class="field"><label>Event type</label>
        <select id="er-type">${(S.lookups.event_types || []).map(t =>
          `<option ${t === e.event_type ? "selected" : ""}>${esc(t)}</option>`).join("")}</select></div>
      <div class="field"><label>Date it took place</label>
        <input type="date" id="er-date" value="${esc(e.event_date || "")}"></div>
      <div class="field"><label>Execution date</label>
        <input type="date" id="er-exec" value="${esc(e.execution_date || "")}"></div>
      <div class="field"><label>Location</label>
        <input id="er-loc" value="${esc(e.location || "")}"></div>
      <div class="field"><label>Attendees</label>
        <input type="number" id="er-att" min="0" value="${e.expected_attendees ?? ""}"></div>
    </div>
    <div class="field"><label>Description</label>
      <textarea id="er-desc" rows="3">${esc(e.description || "")}</textarea></div>
    <div class="field"><label>Notes on execution</label>
      <input id="er-notes" value="${esc(e.execution_notes || "")}"></div>
    ${kindOf(e) !== "activity" ? `<div class="field"><label>Audience</label>
      <input id="er-audience" value="${esc(e.audience || "KABi MENA")}"></div>
    <div class="field full"><label>Approved by</label>
      <div class="picker" id="er-approvers">${(S.lookups.approvers || []).map(a =>
        approverRow(a, (e.approvers || []).some(x => x.id === a.id))).join("")}</div>
      <div class="hint">Who signed this off before it went out.</div></div>` : ""}
    <div class="hint">${icon("info", "icon-sm")}Pictures and announcements are added and
      removed directly on the event page.</div>
  `, "Save record", async () => {
    await api(`/api/events/${e.id}/record`, { method: "PUT", body: {
      event_name: $("#er-name").value.trim(),
      event_type: $("#er-type").value,
      event_date: $("#er-date").value,
      execution_date: $("#er-exec").value,
      location: $("#er-loc").value.trim(),
      expected_attendees: $("#er-att").value || null,
      description: $("#er-desc").value.trim(),
      execution_notes: $("#er-notes").value.trim(),
      audience: $("#er-audience") ? $("#er-audience").value.trim() : null,
      // Sent only for the kinds that show the picker: an absent key means "leave it".
      ...($("#er-approvers")
        ? { approver_ids: orderedRecordApprovers(pickedApprovers("#er-approvers")) } : {}),
    }});
    toast("Record updated", "The history and the calendar now show the new details.");
    route();
    return true;
  });
  bindApproverPicker("#er-approvers");
}

function calDetail(id) {
  const e = (S.archive || []).find(x => x.id === Number(id));
  if (!e) return;
  const photos = e.photos || [];
  openModal(`
  <div class="overlay" id="cd-ov"><div class="modal wide">
    <div class="cd-hero ${e.executed ? "ok" : e.status === "rejected" ? "no" : ""}">
      ${photos.length ? `<img class="cd-hero-img" src="${esc(photos[0].src)}" alt="">` : ""}
      <button class="x-btn cd-x" id="cd-x">${icon("x")}</button>
      <div class="cd-hero-txt">
        <div class="dh-num" style="color:#9fe4ef">${esc(e.event_number)}</div>
        <h2>${esc(e.event_name)}</h2>
        <div class="cd-meta">
          <span>${icon("calendar", "icon-sm")}${fmtDate(e.calendar_date)}</span>
          <span>${icon("mapPin", "icon-sm")}${esc(e.location || "—")}</span>
          ${e.expected_attendees ? `<span>${icon("users", "icon-sm")}${e.expected_attendees} attendees</span>` : ""}
          <span>${icon("ticket", "icon-sm")}${esc(e.event_type || "—")}</span>
        </div>
      </div>
    </div>
    <div class="modal-body">
      <div class="chip-row mb">
        ${statusBadge(e.status)}
        ${e.executed ? `<span class="badge b-executed">${icon("flag")}Executed ${fmtDate(e.execution_date)}</span>` : ""}
        ${vendorSummaryBadge(e.vendor_summary)}
      </div>
      ${e.description ? `<p style="white-space:pre-wrap;margin:0 0 16px">${esc(e.description)}</p>` : ""}
      ${e.execution_notes ? `<div class="good-box mb">${icon("note", "icon-sm")}<b>How it went:</b> ${esc(e.execution_notes)}</div>` : ""}

      ${(e.announcement_files || []).length ? `<div class="chip-row mb">
        ${e.announcement_files.map(f => `<a class="file-chip" href="${esc(f.src)}" target="_blank" rel="noopener">
          ${icon("note", "icon-sm")}<span class="nm">${esc(f.caption || f.file_name)}</span>
          <span class="sz">announcement</span></a>`).join("")}</div>` : ""}

      ${photos.length ? `<div class="section-title">${icon("photo")}Announcement &amp; pictures
          <span class="pill-count">${photos.length}</span></div>
        <div class="cd-gallery mb">${photos.map(p => `
          <figure><img data-api-src="${esc(p.src)}" alt="${esc(p.file_name)}"
            data-act="zoom" data-kind="${esc(p.source || "Event picture")}"
            data-src="${esc(p.src)}">
            <figcaption>${esc(p.caption || p.source || p.file_name)}</figcaption></figure>`).join("")}</div>`
      : `<div class="note-box mb">${icon("photo", "icon-sm")} No pictures were attached to this event.</div>`}

      <div class="kv">
        ${kindOf(e) === "activity" ? `
          ${kvItem("Approved budget", money(e.approved_budget), true)}
          ${kvItem("Total estimated", money(e.total_budget), true)}
          ${kvItem("Approver", (e.approvers || []).map(a => a.name).join(", ") || "—")}
          ${kvItem("Requested by", e.creator_name)}
          ${kvItem("Vendors", (e.vendor_names || []).join(", ") || "—")}
          ${kvItem("Decision date", e.decided_at ? fmtDate(e.decided_at) : "—")}`
        : `
          ${kvItem("Kind", KIND_LABEL[kindOf(e)])}
          ${kvItem("Audience", e.audience || "—")}
          ${kvItem("Approved by", (e.approvers || []).map(a => a.name).join(", ") || "—")}
          ${kvItem("Shared by", e.creator_name)}
          ${kvItem("Sharing date", fmtDate(e.execution_date || e.event_date))}`}
      </div>
    </div>
    <div class="modal-foot">
      <button class="btn" id="cd-close">Close</button>
      ${S.archiveReadOnly ? "" : `<a class="btn btn-primary" href="#/events/${e.id}">${icon("eye")}Open full record</a>`}
    </div>
  </div></div>`);
  const close = () => closeModal();
  $("#cd-x").onclick = close; $("#cd-close").onclick = close;
  $("#cd-ov").onclick = (ev) => { if (ev.target.id === "cd-ov") close(); };
}

/* ------------------------------------------------- archive (Events tab 2) */
function archiveSummary(closed) {
  const executed = closed.filter(e => e.executed);
  const executedBudget = executed.reduce((s, e) => s + Number(e.approved_budget || 0), 0);
  return `<div class="stat-grid">
    ${statCard("c-total", "checks", "Completed Activities", closed.length, "closed records")}
    ${statCard("c-approved", "flag", "Executed", executed.length, "confirmed as delivered")}
    ${statCard("c-rejected", "circleX", "Not executed", closed.length - executed.length, "rejected, cancelled or still to run")}
    ${statCard("c-budget", "wallet", "Executed Budget", money(executedBudget, false), S.settings.currency, true)}
  </div>`;
}

function archiveList(closed) {
  const years = {};
  closed.forEach(e => {
    const y = String(e.execution_date || e.event_date || e.created_at || "").slice(0, 4) || "—";
    (years[y] = years[y] || []).push(e);
  });
  return Object.keys(years).sort().reverse().map(y => `
    <div class="card mb">
      <div class="card-head"><h3>${icon("calendar")}${esc(y)} <span class="pill-count">${years[y].length}</span></h3></div>
      ${years[y].sort((a, b) => String(b.event_date || "").localeCompare(String(a.event_date || ""))).map(e => {
    const parts = fmtDate(e.execution_date || e.event_date).split(" ");
    return `<div class="arch-row">
          <div class="arch-year">${esc(parts[1] || "")}<span>${esc(parts[0] || "")}</span></div>
          <div>
            <a class="link-strong" href="#/events/${e.id}" style="font-size:14.5px">${esc(e.event_name)}</a>
            <span class="mono faint" style="font-size:11.5px;margin-left:8px">${esc(e.event_number)}</span>
            <div class="vc-meta mt" style="gap:12px">
              ${kindBadge(e)}
              ${statusBadge(e.status)}
              ${e.executed ? `<span class="badge b-executed">${icon("flag")}Executed ${fmtDate(e.execution_date)}</span>`
        : `<span class="badge b-neutral">${icon("clock")}Not executed</span>`}
              ${vendorSummaryBadge(e.vendor_summary)}
              <span>${icon("user", "icon-sm")}${esc(e.approver_name || "—")}</span>
              <span>${icon("mapPin", "icon-sm")}${esc(e.location || "—")}</span>
            </div>
          </div>
          <div style="text-align:right">
            <div class="mono" style="font-weight:700;font-size:15px">${money(e.approved_budget || e.total_budget, false)}</div>
            <div class="faint" style="font-size:11px">${e.approved_budget ? "approved" : "estimated"} ${esc(S.settings.currency)}</div>
            <a class="btn btn-sm mt" href="#/events/${e.id}">${icon("eye", "icon-sm")}Open</a>
          </div>
        </div>`;
  }).join("")}
    </div>`).join("");
}

function executionModal(eventId, defaultDate) {
  openModal(`
  <div class="overlay" id="ex-ov"><div class="modal narrow">
    <div class="modal-head"><h3>${icon("flag")}Confirm execution</h3>
      <button class="x-btn" id="ex-x">${icon("x")}</button></div>
    <div class="modal-body">
      <p class="muted" style="margin-top:0">Record that this event actually took place. The approver is notified
        automatically and the event moves to the archive.</p>
      <div class="field"><label>Execution date <span class="req">*</span></label>
        <input type="date" id="ex-date" value="${esc(defaultDate || new Date().toISOString().slice(0, 10))}"></div>
      <div class="field mt"><label>Execution notes</label>
        <textarea id="ex-notes" style="min-height:90px"
          placeholder="Attendance, what went well, anything the approver should know…"></textarea></div>
      <div id="ex-err" class="warn-box mt hidden"></div>
    </div>
    <div class="modal-foot"><button class="btn" id="ex-cancel">Cancel</button>
      <button class="btn btn-good" id="ex-save">${icon("check")}Confirm execution</button></div>
  </div></div>`);
  $("#ex-x").onclick = closeModal; $("#ex-cancel").onclick = closeModal;
  $("#ex-ov").onclick = e => { if (e.target.id === "ex-ov") closeModal(); };
  $("#ex-save").onclick = async () => {
    const date = val("#ex-date");
    if (!date) { const el = $("#ex-err"); el.classList.remove("hidden"); el.textContent = "An execution date is required."; return; }
    try {
      await api(`/api/events/${eventId}/complete`, {
        method: "POST", body: { execution_date: date, execution_notes: val("#ex-notes") }
      });
      closeModal();
      toast("Execution recorded", "The approver has been notified and the event is archived.");
      route();
    } catch (e) { const el = $("#ex-err"); el.classList.remove("hidden"); el.textContent = e.message; }
  };
}

/* ---------------------------------------------------------- notifications */
async function viewNotifications() {
  const d = await api("/api/notifications");
  S.unread = d.unread;
  const tone = (t) => ({
    approval_request: "info", approved: "good", rejected: "bad", partial: "info",
    submitted: "info", withdrawn: "warn", cancelled: "warn", executed: "good",
  }[t] || "info");
  const ic = (t) => ({
    approval_request: "clipboard", approved: "circleCheck", rejected: "circleX",
    partial: "checks", submitted: "send", withdrawn: "refresh", cancelled: "ban", executed: "flag",
  }[t] || "bell");

  setView(`
  <div class="page-head">
    <div><h1>Notifications</h1><p>${d.unread} unread of ${d.notifications.length}.</p></div>
    <div class="head-actions">
      ${d.unread ? `<button class="btn" data-act="read-all">${icon("checks")}Mark all as read</button>` : ""}
    </div>
  </div>
  <div class="card">
    ${d.notifications.length ? d.notifications.map(n => `
      <div class="notif ${n.read ? "" : "unread"}" data-act="open-notif" data-id="${n.id}" data-eid="${n.event_id || ""}">
        <div class="n-ico ${tone(n.type)}">${icon(ic(n.type))}</div>
        <div class="n-body grow">
          <b>${esc(n.title)} ${n.read ? "" : `<span class="unread-dot"></span>`}</b>
          <p>${esc(n.message || "")}</p>
          <div class="t">${fmtDateTime(n.created_at)}${n.event_number ? ` · ${esc(n.event_number)}` : ""}</div>
        </div>
      </div>`).join("")
      : `<div class="empty">${icon("bell", "icon-lg")}<h4>No notifications</h4>
         <p class="muted">You'll be notified here about approval requests and decisions.</p></div>`}
  </div>`);
}

/* --------------------------------------------------------------- profile */
async function viewProfile() {
  const u = S.user;
  const roleText = roleLabel(u);
  setView(`
  <div class="page-head"><div><h1>My Profile</h1><p>Your account details and password.</p></div></div>
  <div class="split">
    <div class="card">
      <div class="card-head"><h3>${icon("user")}Account details</h3></div>
      <div class="card-pad">
        <div class="form-grid">
          <div class="field"><label>Full name</label><input id="pf-name" value="${esc(u.name)}"></div>
          <div class="field"><label>E-mail</label><input value="${esc(u.email)}" readonly></div>
          <div class="field"><label>Department</label><input id="pf-dept" value="${esc(u.department || "")}"></div>
          <div class="field"><label>Phone</label><input id="pf-phone" value="${esc(u.phone || "")}"></div>
          <div class="field"><label>Role</label><input value="${esc(roleText)}" readonly></div>
          <div class="field"><label>Job title</label><input value="${esc(u.job_title || "—")}" readonly></div>
        </div>
        <div class="divider"></div>
        <div class="section-title">${icon("lock")}Change password</div>
        <div class="form-grid">
          <div class="field"><label>Current password</label><input type="password" id="pf-cur" autocomplete="current-password"></div>
          <div class="field"><label>New password</label><input type="password" id="pf-new" autocomplete="new-password">
            <div class="hint">At least 10 characters, and not your own name or e-mail.
              Leave blank to keep your current password. Changing it signs you out
              everywhere else.</div></div>
        </div>
        <button class="btn btn-primary mt" data-act="save-profile">${icon("check")}Save changes</button>
      </div>
    </div>
    <div class="card card-pad">
      <div class="section-title">${icon("shield")}Access</div>
      <p class="muted" style="font-size:13px">
        ${u.role === "admin" ? "You can see and manage everything: all events, users, lists and settings."
      : u.role === "manager" ? "You can see events assigned to you for approval, plus any events you created."
        : u.can_view_all ? "An administrator granted you visibility over all IC events."
          : "You can see the events you created. Ask an administrator if you need broader access."}
      </p>
      <div class="divider"></div>
      <div class="kv">
        ${kvItem("User ID", "#" + u.id, true)}
        ${kvItem("Member since", fmtDate(u.created_at))}
      </div>
      <button class="btn btn-block mt" data-act="logout" style="color:var(--bad);border-color:#f6cdd1">
        ${icon("logout")}Sign out</button>
    </div>
  </div>`);
}

/* ----------------------------------------------------------------- admin */
async function viewAdmin() {
  if (S.user.role !== "admin") throw new Error("Administrator access is required for this page.");
  const tabs = [
    { k: "users", t: "Users", i: "users" },
    { k: "lists", t: "Event types & categories", i: "list" },
    { k: "approvals", t: "Approval records", i: "clipboard" },
    { k: "emails", t: "E-mail outbox", i: "mail" },
    { k: "settings", t: "Settings", i: "settings" },
  ];
  setView(`
  <div class="page-head">
    <div><h1>Administration</h1><p>Users, lists, approval records, e-mail and system settings.</p></div>
    <div class="head-actions">
      <button class="btn btn-primary" data-act="export-xlsx">${icon("fileExport")}Export to Excel</button>
      <button class="btn btn-ghost" data-act="export">${icon("fileExport")}CSV</button></div>
  </div>
  <div class="tabs">${tabs.map(t => `<button data-act="admin-tab" data-tab="${t.k}" class="${S.adminTab === t.k ? "on" : ""}">
    ${icon(t.i, "icon-sm")}${t.t}</button>`).join("")}</div>
  <div id="admin-body">${loadingView()}</div>`);

  const body = $("#admin-body");
  if (S.adminTab === "users") body.innerHTML = await adminUsers();
  else if (S.adminTab === "lists") body.innerHTML = await adminLists();
  else if (S.adminTab === "approvals") body.innerHTML = await adminApprovals();
  else if (S.adminTab === "emails") body.innerHTML = await adminEmails();
  else body.innerHTML = await adminSettings();
  if (S.adminTab === "settings") bindSettings();
}

async function adminUsers() {
  const d = await api("/api/users");
  return `
  <div class="card">
    <div class="card-head"><h3>${icon("users")}Users <span class="pill-count">${d.users.length}</span></h3>
      <button class="btn btn-primary btn-sm" data-act="user-new">${icon("plus", "icon-sm")}Add user</button></div>
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>Name</th><th>E-mail</th><th>Role</th><th>Department</th><th>Manager</th>
        <th class="center">Events</th><th class="center">To approve</th><th class="center">All events</th>
        <th class="center">History</th><th>Status</th><th></th></tr></thead>
      <tbody>${d.users.map(u => `<tr>
        <td><b>${esc(u.name)}</b><div class="faint" style="font-size:11.5px">${esc(u.job_title || "")}</div></td>
        <td class="faint">${esc(u.email)}</td>
        <td><span class="badge ${u.role === "admin" ? "b-completed" : u.role === "manager" ? "b-partial" : "b-neutral"}">${esc(roleLabel(u))}</span>
          <div class="faint" style="font-size:10.5px;margin-top:3px">${u.passwordless
            ? `${icon("mail", "icon-sm")}code by e-mail` : `${icon("lock", "icon-sm")}password`}</div></td>
        <td>${esc(u.department || "—")}</td>
        <td>${esc(u.manager_name || "—")}</td>
        <td class="center mono">${u.events_created}</td>
        <td class="center mono">${u.events_to_approve}</td>
        <td class="center">${u.can_view_all ? icon("check") : "—"}</td>
        <td class="center">${u.can_view_archive ? icon("check") : "—"}</td>
        <td>${u.active ? `<span class="badge b-approved">Active</span>` : `<span class="badge b-cancelled">Inactive</span>`}
          ${u.invited ? `<div class="faint" style="font-size:10.5px;margin-top:3px">${icon("mail", "icon-sm")}invited — no password</div>` : ""}</td>
        <td><div class="chip-row">
          <button class="btn btn-sm" data-act="user-edit" data-u='${esc(JSON.stringify(u))}'
                  title="Edit">${icon("pencil", "icon-sm")}</button>
          <button class="btn btn-sm" data-act="user-password"
                  data-id="${u.id}" data-name="${esc(u.name)}" data-email="${esc(u.email)}"
                  title="Send a link so they can choose their own password">${icon("lock", "icon-sm")}</button>
        </div></td>
      </tr>`).join("")}</tbody></table></div>
  </div>`;
}

function userModal(user) {
  const u = user || {};
  const managers = S.lookups.approvers;
  openModal(`
  <div class="overlay" id="um-ov"><div class="modal">
    <div class="modal-head"><h3>${icon("user")}${user ? "Edit user" : "Add user"}</h3>
      <button class="x-btn" id="um-x">${icon("x")}</button></div>
    <div class="modal-body"><div class="form-grid">
      <div class="field"><label>Full name <span class="req">*</span></label><input id="us-name" value="${esc(u.name || "")}"></div>
      <div class="field"><label>E-mail <span class="req">*</span></label><input type="email" id="us-mail" value="${esc(u.email || "")}"></div>
      <div class="field"><label>Role <span class="req">*</span></label><select id="us-role">
        <option value="ic_user" ${u.role === "ic_user" ? "selected" : ""}>IC member — creates events, signs in with a password</option>
        ${[1, 2, 3, 4, 5, 6].map(n => `<option value="manager:${n}"
          ${u.role === "manager" && (u.approver_level || 1) === n ? "selected" : ""}>${ordinal(n)} approver</option>`).join("")}
        <option value="admin" ${u.role === "admin" ? "selected" : ""}>Administrator (IC) — signs in with a password</option>
      </select>
        <div class="hint" id="us-role-hint"></div></div>
      <div class="field"><label>Department</label><input id="us-dept" value="${esc(u.department || "")}"></div>
      <div class="field"><label>Job title</label><input id="us-title" value="${esc(u.job_title || "")}"></div>
      <div class="field"><label>Phone</label><input id="us-phone" value="${esc(u.phone || "")}"></div>
      <div class="field"><label>Reports to</label><select id="us-mgr"><option value="">—</option>
        ${managers.filter(m => m.id !== u.id).map(m => `<option value="${m.id}" ${String(u.manager_id) === String(m.id) ? "selected" : ""}>${esc(m.name)}</option>`).join("")}</select></div>
      <div class="field full" id="us-pass-wrap">
        <div class="info-box">${icon("lock", "icon-sm")}
          <span>You do not set a password here. Saving this gives you a one-time link to
          send them, and they choose their own — nobody else ever knows it.</span></div></div>
      <div class="field full hidden" id="us-final-wrap"><label>Final approval</label>
        <label class="flex" style="font-weight:500"><input type="checkbox" id="us-final" style="width:auto"
          ${u.is_final_approver ? "checked" : ""}>
          <span>This approver gives the <b>final approval</b> — their decision closes the request</span></label>
        <div class="hint">Only one person can hold this. Ticking it moves the final say to them.</div></div>
      <div class="field full"><label>Permissions</label>
        <label class="flex" style="font-weight:500"><input type="checkbox" id="us-all" style="width:auto" ${u.can_view_all ? "checked" : ""}>
          Can view <b>all</b> events (not only their own)</label>
        <label class="flex mt" style="font-weight:500"><input type="checkbox" id="us-arch" style="width:auto" ${u.can_view_archive ? "checked" : ""}>
          Can view the <b>completed-events history</b> — read only, calendar + pictures</label>
        <label class="flex mt" style="font-weight:500"><input type="checkbox" id="us-active" style="width:auto" ${user ? (u.active ? "checked" : "") : "checked"}>
          Account is active</label></div>
    </div><div id="um-err" class="warn-box mt hidden"></div></div>
    <div class="modal-foot">
      ${user ? `<button class="btn" id="um-del" style="color:var(--bad);border-color:#f6cdd1;margin-right:auto">
        ${icon("trash")}Delete user</button>` : ""}
      <button class="btn" id="um-cancel">Cancel</button>
      <button class="btn btn-primary" id="um-save">${icon("check")}Save</button></div>
  </div></div>`);
  $("#um-x").onclick = closeModal; $("#um-cancel").onclick = closeModal;
  $("#um-ov").onclick = e => { if (e.target.id === "um-ov") closeModal(); };

  // Everyone can hold a password now, chosen by themselves; approvers can also work
  // straight from the link in their request without ever setting one.
  const syncRole = () => {
    const v = val("#us-role");
    $("#us-final-wrap").classList.toggle("hidden", !v.startsWith("manager"));
    $("#us-role-hint").textContent = v === "admin"
      ? "Signs in with e-mail and password. Can manage users, lists and settings."
      : v === "ic_user"
        ? "Creates events, adds vendors and submits for approval. Signs in with e-mail and password."
        : `Receives approval requests as the ${ordinal(Number(v.split(":")[1]))} approver. `
        + "Reviews straight from the link in the e-mail — no password needed, though "
        + "they can set one to browse the hub.";
  };
  $("#us-role").onchange = syncRole; syncRole();

  const del = $("#um-del");
  if (del) del.onclick = async () => {
    const ok = await confirmDialog({
      title: `Delete ${esc(user.name)}?`,
      message: "The account is removed permanently. If this person already created events or made " +
        "approval decisions, deletion is refused — deactivate them instead so the history stays intact.",
      confirmLabel: "Delete user", tone: "bad",
    });
    if (!ok) return;
    try {
      const d = await api("/api/users/" + user.id, { method: "DELETE" });
      closeModal(); await loadLookups(); toast("User deleted", d.message); viewAdmin();
    } catch (e) {
      const el = $("#um-err"); el.classList.remove("hidden");
      el.innerHTML = `${icon("alert", "icon-sm")} ${esc(e.message)}`;
      if (e.data && e.data.suggest === "deactivate") $("#us-active").checked = false;
    }
  };
  $("#um-save").onclick = async () => {
    const picked = val("#us-role");                     // "ic_user" | "manager:2" | "admin"
    const body = {
      name: val("#us-name"), email: val("#us-mail"),
      role: picked.startsWith("manager") ? "manager" : picked,
      approver_level: picked.startsWith("manager") ? Number(picked.split(":")[1]) : null,
      department: val("#us-dept"),
      job_title: val("#us-title"), phone: val("#us-phone"), manager_id: val("#us-mgr") || null,
      can_view_all: $("#us-all").checked, can_view_archive: $("#us-arch").checked,
      is_final_approver: $("#us-final").checked,
      active: $("#us-active").checked,
    };
    try {
      const out = user
        ? await api("/api/users/" + user.id, { method: "PUT", body })
        : await api("/api/users", { method: "POST", body });
      closeModal(); await loadLookups(); toast("Saved", `${body.name} has been ${user ? "updated" : "added"}.`);
      viewAdmin();
      // The link is shown once, so it is handed over now or made again later.
      if (out && out.setup_url) {
        showSetupLink(body.name, body.email, out.setup_url, out.setup_days, false);
      }
    } catch (e) { const el = $("#um-err"); el.classList.remove("hidden"); el.textContent = e.message; }
  };
}

async function adminLists() {
  const d = await api("/api/admin/lookups");
  const group = (kind, title, ic) => {
    const items = d.items.filter(i => i.kind === kind);
    return `<div class="card mb">
      <div class="card-head"><h3>${icon(ic)}${title} <span class="pill-count">${items.filter(i => i.active).length}</span></h3></div>
      <div class="card-pad">
        <div class="chip-row mb">${items.map(i => `<span class="file-chip" style="${i.active ? "" : "opacity:.45"}">
          <span class="nm">${esc(i.value)}</span>
          ${i.active ? `<button class="btn btn-sm btn-ghost" style="color:var(--bad);padding:0 4px"
            data-act="del-lookup" data-id="${i.id}">${icon("x", "icon-sm")}</button>` : `<span class="sz">removed</span>`}
        </span>`).join("")}</div>
        <div class="toolbar">
          <input id="lk-${kind}" placeholder="Add a new entry…" class="grow"
            style="padding:9px 11px;border:1px solid var(--line-2);border-radius:10px">
          <button class="btn btn-sm" data-act="add-lookup" data-kind="${kind}">${icon("plus", "icon-sm")}Add</button>
        </div>
      </div></div>`;
  };
  return group("event_type", "Event types", "ticket") + group("vendor_category", "Vendor categories", "store");
}

async function adminApprovals() {
  const d = await api("/api/admin/approvals");
  return `
  <div class="card mb">
    <div class="card-head"><h3>${icon("clipboard")}Event approval records <span class="pill-count">${d.approvals.length}</span></h3></div>
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>Event</th><th>Approver</th><th>Decision</th><th>Reason</th><th>Decision date</th>
        <th class="right">Total</th><th class="right">Approved</th><th>Status</th></tr></thead>
      <tbody>${d.approvals.map(a => `<tr>
        <td><a class="link-strong" href="#/events/${a.event_id}">${esc(a.event_number)}</a>
          <div class="faint" style="font-size:11.5px">${esc(a.event_name)}</div></td>
        <td>${esc(a.approver_name || "—")}</td>
        <td>${a.event_decision ? `<span class="badge ${a.event_decision === "approved" ? "b-approved" : a.event_decision === "rejected" ? "b-rejected" : "b-cancelled"}">${esc(a.event_decision)}</span>` : `<span class="badge b-pending">open</span>`}</td>
        <td class="faint" style="max-width:260px">${esc(a.event_rejection_reason || "—")}</td>
        <td class="nowrap faint">${a.decision_date ? fmtDateTime(a.decision_date) : "—"}</td>
        <td class="right mono">${money(a.total_budget, false)}</td>
        <td class="right mono">${money(a.approved_budget, false)}</td>
        <td>${statusBadge(a.event_status)}</td></tr>`).join("") ||
    `<tr><td colspan="8" class="center muted" style="padding:30px">No approval records yet.</td></tr>`}</tbody></table></div>
  </div>

  <div class="card mb">
    <div class="card-head"><h3>${icon("store")}Vendor decisions <span class="pill-count">${d.vendor_approvals.length}</span></h3></div>
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>Event</th><th>Vendor</th><th class="right">Amount</th><th>Decision</th><th>Reason</th><th>Approver</th><th>Date</th></tr></thead>
      <tbody>${d.vendor_approvals.map(v => `<tr>
        <td class="mono"><a class="link-strong" href="#/events/${v.event_id}">${esc(v.event_number)}</a></td>
        <td>${esc(v.vendor_name)}</td>
        <td class="right mono">${money(v.total_amount, false)}</td>
        <td>${vendorBadge(v.decision)}</td>
        <td class="faint" style="max-width:260px">${esc(v.rejection_reason || "—")}</td>
        <td>${esc(v.approver_name || "—")}</td>
        <td class="nowrap faint">${fmtDateTime(v.decision_date)}</td></tr>`).join("") ||
    `<tr><td colspan="7" class="center muted" style="padding:30px">No vendor decisions yet.</td></tr>`}</tbody></table></div>
  </div>

  <div class="card">
    <div class="card-head"><h3>${icon("history")}Full approval history <span class="pill-count">${d.history.length}</span></h3></div>
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>Event</th><th>Action</th><th>User</th><th>Role</th><th>Previous → New</th><th>Comment</th><th>Date & time</th></tr></thead>
      <tbody>${d.history.map(h => `<tr>
        <td class="mono"><a class="link-strong" href="#/events/${h.event_id}">${esc(h.event_number)}</a></td>
        <td><b>${esc(h.action)}</b></td>
        <td>${esc(h.performer_name || "System")}</td>
        <td class="faint">${esc((h.role || "").replace("_", " "))}</td>
        <td class="faint nowrap">${h.previous_status ? esc((STATUS[h.previous_status] || {}).label || h.previous_status) : "—"}
          → ${h.new_status ? esc((STATUS[h.new_status] || {}).label || h.new_status) : "—"}</td>
        <td class="faint" style="max-width:280px">${esc(h.comments || "—")}</td>
        <td class="nowrap faint">${fmtDateTime(h.created_at)}</td></tr>`).join("")}</tbody></table></div>
  </div>`;
}

async function adminEmails() {
  const d = await api("/api/admin/emails");
  const st = { queued: "b-pending", sent: "b-approved", failed: "b-rejected", sent_manually: "b-partial" };
  const typeLabel = {
    approval_request: "1 · Approval request", fully_approved: "2 · Fully approved",
    partially_approved: "3 · Partially approved", rejected: "4 · Rejected",
    executed: "5 · Executed", test: "Test message",
  };
  return `
  <div class="card card-pad mb">
    <div class="note-box">${icon("info", "icon-sm")} Every message the system generates is stored here — approval
      requests to the manager, and approval / rejection / execution notifications back to the requester.
      They are delivered automatically once SMTP is enabled in <b>Settings</b>; until then they are held as
      <b>queued</b>. Open any message to see exactly what the recipient receives.</div>
  </div>
  <div class="card">
    <div class="card-head"><h3>${icon("mail")}E-mail outbox <span class="pill-count">${d.emails.length}</span></h3></div>
    <div class="table-wrap"><table class="tbl">
      <thead><tr><th>Type</th><th>Subject</th><th>Recipient</th><th>Status</th><th>Created</th><th></th></tr></thead>
      <tbody>${d.emails.map(m => `<tr>
        <td class="nowrap">${esc(typeLabel[m.type] || m.type)}</td>
        <td><b>${esc(m.subject)}</b></td>
        <td class="faint">${esc(m.to_name || "")}<br>${esc(m.to_email)}</td>
        <td><span class="badge ${st[m.status] || "b-neutral"}">${esc(m.status)}</span>
          ${m.error ? `<div class="faint" style="font-size:11px;max-width:200px">${esc(m.error)}</div>` : ""}</td>
        <td class="nowrap faint">${fmtDateTime(m.created_at)}</td>
        <td><div class="row-actions">
          <button class="btn btn-sm" data-act="mail-view" data-id="${m.id}">${icon("eye", "icon-sm")}Preview</button>
          <button class="btn btn-sm btn-primary" data-act="admin-eml" data-id="${m.id}"
                  data-name="${esc(m.subject)}"
                  title="Download as an Outlook draft with the full design">${icon("mail", "icon-sm")}Designed</button>
          <a class="btn btn-sm btn-ghost" href="${mailtoHref(m)}" title="Plain-text mailto">${icon("send", "icon-sm")}</a>
          <button class="btn btn-sm" data-act="copy-mail" data-mid="${m.id}" title="Copy with formatting">${icon("paperclip", "icon-sm")}</button>
        </div></td>
      </tr>`).join("") || `<tr><td colspan="6" class="center muted" style="padding:30px">No messages yet.</td></tr>`}
      </tbody></table></div>
  </div>`;
}

async function adminSettings() {
  const d = await api("/api/admin/settings");
  const s = d.settings;
  return `
  <div class="split">
    <div class="stack">
      <div class="card">
        <div class="card-head"><h3>${icon("settings")}General</h3></div>
        <div class="card-pad"><div class="form-grid">
          <div class="field"><label>Organisation name</label><input id="se-org" value="${esc(s.org_name)}"></div>
          <div class="field"><label>Application name</label><input id="se-app" value="${esc(s.app_name)}"></div>
          <div class="field"><label>Currency</label><input id="se-cur" value="${esc(s.currency)}" maxlength="6"></div>
          <div class="field"><label>Default VAT rate (%)</label><input type="number" step="0.01" id="se-vat" value="${esc(s.default_vat_rate)}"></div>
          <div class="field full"><label>Application base URL</label><input id="se-url" value="${esc(s.app_base_url)}">
            <div class="hint">Used to build the “Review Event” button inside approval e-mails.</div></div>
        </div></div>
      </div>

      <div class="card">
        <div class="card-head"><h3>${icon("mail")}E-mail delivery (SMTP)</h3>
          <span class="badge ${s.smtp_enabled === "1" ? "b-approved" : "b-draft"}">
            ${s.smtp_enabled === "1" ? "Enabled" : "Disabled"}</span></div>
        <div class="card-pad">
          <div class="${s.smtp_enabled === "1" ? "good-box" : "warn-box"} mb">${icon(s.smtp_enabled === "1" ? "circleCheck" : "alert", "icon-sm")}
            ${s.smtp_enabled === "1"
      ? "<b>Live.</b> Approval requests and decision notifications are being e-mailed automatically to the manager and the requester."
      : "<b>Disabled.</b> Messages are composed and stored in the outbox only — nothing is sent. Fill in the SMTP details below, tick “Enable SMTP delivery”, save, then send a test message."}</div>
          <label class="flex mb" style="font-weight:600"><input type="checkbox" id="se-smtp" style="width:auto"
            ${s.smtp_enabled === "1" ? "checked" : ""}> Enable SMTP delivery</label>
          <div class="form-grid">
            <div class="field"><label>SMTP host</label><input id="se-host" value="${esc(s.smtp_host)}" placeholder="smtp.office365.com"></div>
            <div class="field"><label>Port</label><input id="se-port" value="${esc(s.smtp_port)}"></div>
            <div class="field"><label>Username</label><input id="se-user" value="${esc(s.smtp_user)}" autocomplete="off"></div>
            <div class="field"><label>Password</label><input type="password" id="se-pass" value="${esc(s.smtp_password)}" autocomplete="new-password"></div>
            <div class="field"><label>From address</label><input id="se-from" value="${esc(s.mail_from)}"></div>
            <div class="field"><label>From name</label><input id="se-fromname" value="${esc(s.mail_from_name)}"></div>
            <div class="field full"><label class="flex" style="font-weight:500">
              <input type="checkbox" id="se-tls" style="width:auto" ${s.smtp_tls === "1" ? "checked" : ""}> Use STARTTLS (port 465 uses implicit SSL automatically)</label></div>
          </div>
          <div class="divider"></div>
          <div class="section-title">${icon("send")}Send a test message</div>
          <div class="toolbar">
            <input id="se-test" class="grow" placeholder="recipient@kabi.ai" value="${esc(S.user.email)}"
              style="padding:9px 11px;border:1px solid var(--line-2);border-radius:10px">
            <button class="btn" id="se-test-btn">${icon("mail", "icon-sm")}Send test</button>
          </div>
          <div class="hint">Save your settings first. The test only works while SMTP delivery is enabled.</div>
        </div>
      </div>
      <button class="btn btn-primary" id="se-save">${icon("check")}Save settings</button>
    </div>

    <div class="card card-pad">
      <div class="section-title">${icon("shield")}Security model</div>
      <ul class="muted" style="font-size:12.8px;padding-left:18px;line-height:1.9;margin:0">
        <li>Every request is authorised on the server — changing an ID in the URL returns 403.</li>
        <li>IC Users see only their own events unless you grant “view all”.</li>
        <li>Managers see only events assigned to them for approval.</li>
        <li>Only the assigned approver can open an approval page or submit a decision.</li>
        <li>Passwords are stored as PBKDF2-SHA256 hashes (120k iterations).</li>
        <li>Sessions are HttpOnly cookies valid for 7 days.</li>
        <li>Uploads are limited to ${limitLabel()} and a safe extension whitelist.</li>
      </ul>
    </div>
  </div>`;
}

function bindSettings() {
  const test = $("#se-test-btn");
  if (test) test.onclick = async () => {
    const to = val("#se-test");
    const ok = await confirmDialog({
      title: "Send a test e-mail?",
      message: `A real message will be delivered to <b>${esc(to)}</b> through the configured SMTP server.`,
      confirmLabel: "Send test", tone: "primary",
    });
    if (!ok) return;
    test.disabled = true; test.innerHTML = "Sending…";
    try { const d = await api("/api/admin/test-email", { method: "POST", body: { to } }); toast("Test sent", d.message); }
    catch (e) { apiError(e); }
    test.disabled = false; test.innerHTML = `${icon("mail", "icon-sm")}Send test`;
  };
  const btn = $("#se-save"); if (!btn) return;
  btn.onclick = async () => {
    try {
      await api("/api/admin/settings", {
        method: "PUT", body: {
          org_name: val("#se-org"), app_name: val("#se-app"), currency: val("#se-cur"),
          default_vat_rate: val("#se-vat"), app_base_url: val("#se-url"),
          smtp_enabled: $("#se-smtp").checked, smtp_host: val("#se-host"), smtp_port: val("#se-port"),
          smtp_user: val("#se-user"), smtp_password: $("#se-pass").value, smtp_tls: $("#se-tls").checked,
          mail_from: val("#se-from"), mail_from_name: val("#se-fromname"),
        }
      });
      S.settings.currency = val("#se-cur");
      toast("Settings saved", "Your changes take effect immediately.");
      viewAdmin();
    } catch (e) { apiError(e); }
  };
}

async function previewEmail(id) {
  try {
    const d = await api("/api/admin/emails/" + id);
    const m = d.email;
    // While SMTP is off the recipient can't click anything, so expose the review
    // link here for testing the manager journey end to end.
    const link = (m.body_html.match(/https?:\/\/[^\s"'<>]*#\/review\/[A-Za-z0-9_\-]{20,}/) || [])[0];
    openModal(`<div class="overlay" id="em-ov"><div class="modal wide">
      <div class="modal-head"><h3>${icon("mail")}${esc(m.subject)}</h3>
        <button class="x-btn" id="em-x">${icon("x")}</button></div>
      <div class="modal-body">
        <div class="kv mb">
          ${kvItem("To", `${m.to_name || ""} <${m.to_email}>`)}
          ${kvItem("Status", m.status)}
          ${kvItem("Created", fmtDateTime(m.created_at))}
          ${kvItem("Sent", m.sent_at ? fmtDateTime(m.sent_at) : "—")}
        </div>
        <div class="good-box mb">${icon("mail", "icon-sm")}
          <b>Send this exact design.</b> <a href="#" data-act="admin-eml" data-id="${id}"
          data-name="${esc(m.subject)}">Download it as an Outlook draft</a>
          and press Send, or use <b>Copy formatted</b> and paste into a new message.</div>
        ${link ? `<div class="note-box mb">${icon("link", "icon-sm")}
          <b>Review link for ${esc(m.to_name || m.to_email)}.</b> The button inside the preview below can't be
          clicked while SMTP is off — use this to walk the manager's journey yourself.
          <div class="chip-row mt">
            <a class="btn btn-sm btn-cyan" href="${esc(link)}" target="_blank" rel="noopener">
              ${icon("externalLink", "icon-sm")}Open review link</a>
            <button class="btn btn-sm" id="em-copy">${icon("paperclip", "icon-sm")}Copy link</button>
          </div></div>` : ""}
        <div class="email-preview"><iframe id="em-frame" sandbox=""></iframe></div>
      </div>
      <div class="modal-foot">
        <button class="btn" id="em-close">Close</button>
        <button class="btn btn-primary" id="em-send">${icon("send")}Send now</button>
      </div></div></div>`);
    const copyBtn = $("#em-copy");
    if (copyBtn) copyBtn.onclick = () => {
      if (copyPlain(link)) toast("Link copied", "Paste it into any browser to open the approval page.");
      else toast("Copy blocked", "Select the link text and copy it manually.", "err");
    };
    $("#em-frame").srcdoc = m.body_html;
    $("#em-x").onclick = closeModal; $("#em-close").onclick = closeModal;
    $("#em-ov").onclick = e => { if (e.target.id === "em-ov") closeModal(); };
    $("#em-send").onclick = async () => {
      const ok = await confirmDialog({
        title: "Send this e-mail now?",
        message: `This will deliver a real e-mail to <b>${esc(m.to_email)}</b> using the configured SMTP server.`,
        confirmLabel: "Send e-mail", tone: "primary",
      });
      if (!ok) return;
      try { await api(`/api/admin/emails/${id}/send`, { method: "POST" }); toast("Sent", "The message was delivered."); viewAdmin(); }
      catch (e) { apiError(e); }
    };
  } catch (e) { apiError(e); }
}

/* -------------------------------------------------------- global actions */
/* A <select> announces itself with change, not click, so the period control is wired
   here rather than through the click dispatcher everything else uses. */
document.addEventListener("change", (ev) => {
  if (!ev.target.closest("[data-act='dash-period']")) return;
  S.dashYear = val("#dash-year");
  S.dashMonth = S.dashYear === "all" ? "all" : val("#dash-month");
  route();
});

document.addEventListener("click", async (ev) => {
  const zoom = ev.target.closest("[data-act='zoom']");
  if (zoom) {
    // Everything in the same gallery travels with it, so the arrows work.
    const box = zoom.closest(".cd-gallery, .thumb-row, .lb-group") || document;
    const group = [...box.querySelectorAll("[data-act='zoom']")].map(el => ({
      src: el.dataset.src,
      kind: el.dataset.kind || "",
      name: el.getAttribute("alt") || "",
      // a figcaption carries the markup's own whitespace, which reads badly in a title
      caption: ((el.closest("figure") || {}).querySelector
        ? ((el.closest("figure").querySelector("figcaption") || {}).textContent || "")
        : "").replace(/\s+/g, " ").trim(),
    }));
    const here = group.find(g => g.src === zoom.dataset.src) || zoom.dataset.src;
    lightbox(here, group);
    return;
  }
  const el = ev.target.closest("[data-act]");
  if (!el) {
    if (S.menuOpen && !ev.target.closest(".user-menu")) { S.menuOpen = false; const m = $("#umenu"); if (m) m.remove(); }
    return;
  }
  const act = el.dataset.act;

  switch (act) {
    case "usermenu": S.menuOpen = !S.menuOpen; route(); break;
    case "go": location.hash = el.dataset.href; break;
    case "bell": location.hash = "#/notifications"; break;
    case "forget-device": {
      const ok = await confirmDialog({
        title: "Forget this device?",
        message: "You'll be asked to confirm your name and e-mail again the next time you open an " +
          "approval link. Use this on shared or public computers.",
        confirmLabel: "Forget this device", tone: "bad",
      });
      if (!ok) return;
      await api("/api/review/forget", { method: "POST" }).catch(() => { });
      S.user = null; S.scoped = false; S.scopeEventId = null; S.decision = null;
      location.hash = ""; renderLogin();
      toast("Device forgotten", "You'll be asked to confirm next time."); break;
    }
    case "logout":
      await api("/api/auth/logout", { method: "POST" }).catch(() => { });
      S.user = null; S.decision = null; S.scoped = false; S.scopeEventId = null;
      location.hash = ""; renderLogin(); break;

    case "create-event": createEvent(); break;
    case "save-event": saveEvent(el.dataset.id); break;
    case "submit-event": submitEvent(el.dataset.id); break;
    case "clear-filters":
      S.filters = { search: "", status: "all", vendor_status: "all", event_type: "all", approver: "", date_from: "", date_to: "" };
      viewEvents(); break;
    case "events-tab": S.eventsTab = el.dataset.tab; viewEvents(); break;
    case "kind-filter":
      S.recordKind = el.dataset.kind || "all";
      route();
      return;

    case "unshare": {
      try {
        await api(`/api/events/${el.dataset.eid}/messages/${el.dataset.mid}/mark-sent`,
                  { method: "POST", body: { shared: false } });
        toast("Ready to send again", `${el.dataset.name} is back on the list.`);
        route();
      } catch (err) { toast("Could not do that", err.message, "bad"); }
      return;
    }

    case "copy-link": {
      const { url, name } = el.dataset;
      try {
        await navigator.clipboard.writeText(url);
        toast("Link copied", `Send it to ${name}. It opens the approval page for them.`);
      } catch (err) {
        const box = el.closest(".send-row").querySelector(".link-box");
        if (box) { box.select(); }
        toast("Select and copy", "Your browser blocked the clipboard.", "warn");
      }
      return;
    }

    case "rebuild-msg": {
      try {
        const out = await api(`/api/events/${el.dataset.eid}/messages/rebuild`,
                              { method: "POST", body: {} });
        toast("Draft rebuilt", `${(out.names || []).join(", ") || "Nobody"} — the message now `
          + "carries the event exactly as it stands. The link is unchanged.");
        route();
      } catch (err) { toast("Could not rebuild it", err.message, "bad"); }
      return;
    }

    case "arch-view": S.archiveView = el.dataset.v; viewEvents(); break;
    case "user-password": {
      const { id, name, email } = el.dataset;
      const ok = await confirmDialog({
        title: "Send a set-up link?",
        message: `<b>${esc(name)}</b> will get a one-time link and choose their own password. ` +
                 "Any earlier link stops working. Their current password, if they have one, " +
                 "keeps working until they set a new one.",
        confirmLabel: "Make the link", tone: "primary",
      });
      if (!ok) return;
      try {
        const out = await api(`/api/users/${id}/setup-link`, { method: "POST", body: {} });
        showSetupLink(out.name, out.email, out.url, out.days, out.sent);
      } catch (e) { toast("Could not make the link", e.message, "bad"); }
      return;
    }

    case "reload-app": location.reload(); return;
    case "add-completed": addCompletedForm(); break;
    case "edit-record": {
      const { event: ev } = await api(`/api/events/${el.dataset.id}`);
      editRecordForm(ev);
      return;
    }
    case "ce-add-vendor": ceVendorRow(); break;
    case "ce-del-vendor": el.closest(".ce-vrow").remove(); break;
    case "cal-open": calDetail(el.dataset.id); break;
    case "del-photo": {
      const ok = await confirmDialog({
        title: "Remove this picture?", message: "It will be deleted from the event gallery.",
        confirmLabel: "Remove", tone: "bad",
      });
      if (!ok) return;
      try { await api("/api/event-photos/" + el.dataset.pid, { method: "DELETE" }); toast("Removed", ""); route(); }
      catch (e) { apiError(e); } break;
    }
    case "cal-today": S.calMonth = null; renderCalendar(); break;
    case "cal-move": {
      const cur = S.calMonth ? new Date(S.calMonth + "-01T00:00:00") : new Date();
      cur.setMonth(cur.getMonth() + Number(el.dataset.by));
      S.calMonth = monthKey(cur); renderCalendar(); break;
    }

    case "show-add-approver": {
      $("#ap-form").classList.remove("hidden"); el.classList.add("hidden"); $("#ap-name").focus(); break;
    }
    case "add-approver": {
      const err = $("#ap-err");
      const name = val("#ap-name"), email = val("#ap-mail");
      err.classList.add("hidden");
      if (!name || !email) { err.classList.remove("hidden"); err.textContent = "Enter both a name and an e-mail address."; return; }
      try {
        const d = await api("/api/approvers", { method: "POST", body: { name, email } });
        await loadLookups();
        const box = $("#ev-approvers");
        const existing = box.querySelector(`input[value="${d.user.id}"]`);
        if (existing) {
          existing.checked = true;
          existing.closest(".picker-row").classList.add("on");
          toast("Already on the list", `${d.user.name} was already available — now ticked.`);
        } else {
          const empty = box.querySelector(".muted"); if (empty) empty.remove();
          box.insertAdjacentHTML("beforeend", approverRow(d.user, true));
          toast(d.created ? "Approver added" : "Approver selected",
            d.created ? `${d.user.name} will receive the approval e-mail — no account needed.` : d.user.name);
        }
        $("#ap-name").value = ""; $("#ap-mail").value = "";
      } catch (e) { err.classList.remove("hidden"); err.textContent = e.message; }
      break;
    }
    case "export-xlsx": {
      toast("Preparing the workbook", "Every event, vendor, decision and file.");
      try {
        await download("/api/export/events.xlsx");
      } catch (err) { toast("Export failed", err.message, "bad"); }
      return;
    }

    case "export": {
      try {
        await download("/api/export/events.csv");
      } catch (err) { toast("Export failed", err.message, "bad"); }
      return;
    }

    case "add-vendor": {
      const id = el.dataset.id;
      if ($("#ev-name")) { const ok = await api("/api/events/" + id, { method: "PUT", body: readEventForm() }).then(() => true).catch(e => { apiError(e); return false; }); if (!ok) return; }
      vendorModal(id, null, Number(el.dataset.oid) || null); break;
    }
    case "add-option": {
      const id = el.dataset.id;
      if ($("#ev-name")) await api("/api/events/" + id, { method: "PUT", body: readEventForm() }).catch(() => { });
      const name = await confirmDialog({
        title: "Add an alternative option",
        message: "An option is a complete alternative package for the same event — its own vendors and its own " +
          "total. The approver picks exactly one.",
        confirmLabel: "Add option", tone: "primary",
        requireText: "Option name", placeholder: "e.g. Option B — reduced scope",
      });
      if (!name) return;
      try { await api(`/api/events/${id}/options`, { method: "POST", body: { name } }); toast("Option added", name); viewEdit(); }
      catch (e) { apiError(e); } break;
    }
    case "rename-option": {
      const name = await confirmDialog({
        title: "Rename option",
        message: `Currently <b>${esc(el.dataset.name)}</b>. Give it a name the approver will understand.`,
        confirmLabel: "Save name", tone: "primary",
        requireText: "Option name", placeholder: "e.g. Option B — premium package",
      });
      if (!name) return;
      try { await api("/api/options/" + el.dataset.oid, { method: "PUT", body: { name, description: el.dataset.desc } }); viewEdit(); }
      catch (e) { apiError(e); } break;
    }
    case "del-option": {
      const ok = await confirmDialog({
        title: `Remove ${el.dataset.name}?`,
        message: "This option and every vendor, quotation and file inside it will be permanently deleted.",
        confirmLabel: "Remove option", tone: "bad",
      });
      if (!ok) return;
      try { await api("/api/options/" + el.dataset.oid, { method: "DELETE" }); toast("Option removed", ""); viewEdit(); }
      catch (e) { apiError(e); } break;
    }
    case "edit-vendor": {
      const d = await api("/api/events/" + S.route.id);
      const v = d.event.options.flatMap(o => o.vendors).find(x => x.id === Number(el.dataset.vid));
      vendorModal(d.event.id, v, v ? v.option_id : null, d.event.options); break;
    }
    case "del-vendor": {
      const ok = await confirmDialog({
        title: "Remove this vendor?", message: "The vendor, its quotation and all attached files will be deleted from this event.",
        confirmLabel: "Remove vendor", tone: "bad",
      });
      if (!ok) return;
      try { await api("/api/vendors/" + el.dataset.vid, { method: "DELETE" }); toast("Vendor removed", ""); viewEdit(); }
      catch (e) { apiError(e); } break;
    }
    case "del-file":
      try { await api("/api/vendor-files/" + el.dataset.fid, { method: "DELETE" }); closeModal(); toast("File removed", ""); viewEdit(); }
      catch (e) { apiError(e); } break;
    case "del-link":
      try { await api("/api/vendor-links/" + el.dataset.lid, { method: "DELETE" }); el.closest(".link-chip").remove(); }
      catch (e) { apiError(e); } break;

    case "delete-event": {
      const ok = await confirmDialog({
        title: "Delete this draft?", message: "The event, its vendors and all uploaded files will be permanently removed.",
        confirmLabel: "Delete draft", tone: "bad",
      });
      if (!ok) return;
      try { await api("/api/events/" + el.dataset.id, { method: "DELETE" }); toast("Draft deleted", ""); location.hash = "#/events"; }
      catch (e) { apiError(e); } break;
    }
    case "withdraw": {
      const ok = await confirmDialog({
        title: "Withdraw & edit?",
        message: "The pending approval request will be cancelled and the event returns to <b>Draft</b> so you can edit it. " +
          "The approval history is preserved and you will need to submit it again.",
        confirmLabel: "Withdraw & Edit", tone: "primary",
        requireText: "Reason (optional note for the history)", placeholder: "e.g. Vendor updated their quotation",
      });
      if (ok === null) return;
      try {
        await api(`/api/events/${el.dataset.id}/withdraw`, { method: "POST", body: { comment: ok === true ? "" : ok } });
        toast("Withdrawn", "The event is back in draft — you can edit it now.");
        location.hash = `#/events/${el.dataset.id}/edit`; route();
      } catch (e) { apiError(e); } break;
    }
    case "cancel": {
      const reason = await confirmDialog({
        title: "Cancel this event?", message: "The event will be closed and marked as cancelled.",
        confirmLabel: "Cancel event", tone: "bad", requireText: "Cancellation reason", placeholder: "Why is this event cancelled?",
      });
      if (!reason) return;
      try { await api(`/api/events/${el.dataset.id}/cancel`, { method: "POST", body: { reason } }); toast("Event cancelled", ""); route(); }
      catch (e) { apiError(e); } break;
    }
    case "complete": executionModal(el.dataset.id, el.dataset.date); break;

    case "vn-pick": {
      const vid = el.dataset.vid;
      const cur = S.decision.vendors[vid] || { decision: null, reason: "" };
      S.decision.vendors[vid] = {
        decision: cur.decision === "approved" ? null : "approved",
        reason: cur.decision === "approved" ? cur.reason : "",
      };
      S.decision.event = "approved";
      const d = await api("/api/events/" + S.route.id); renderApprovePage(d.event); break;
    }
    case "reject-all": {
      const reason = await confirmDialog({
        title: "Reject this request?",
        message: "The whole request is rejected and the chain stops here. The requester is notified " +
          "with your reason and can revise and resubmit.",
        confirmLabel: "Reject request", tone: "bad",
        requireText: "Reason for rejection", placeholder: "e.g. Budget not available this quarter.",
      });
      if (!reason) return;
      S.decision.event = "rejected";
      S.decision.reason = reason;
      submitDecision(); break;
    }
    case "pick-option": {
      const oid = Number(el.dataset.oid);
      const set = new Set(S.decision.options || []);
      if (set.has(oid)) {
        // Untick: the vendor decisions inside it are no longer part of anything.
        set.delete(oid);
        const d0 = await api("/api/events/" + S.route.id);
        (d0.event.options.find(o => o.id === oid) || { vendors: [] }).vendors
          .forEach(v => { S.decision.vendors[v.id] = { decision: null, reason: "" }; });
        S.decision.options = [...set];
        renderApprovePage(d0.event);
        break;
      }
      set.add(oid);
      S.decision.options = [...set];
      const d = await api("/api/events/" + S.route.id); renderApprovePage(d.event); break;
    }
    case "submit-decision": submitDecision(); break;

    case "open-notif": {
      const id = el.dataset.id, eid = el.dataset.eid;
      await api(`/api/notifications/${id}/read`, { method: "POST" }).catch(() => { });
      if (eid) location.hash = `#/events/${eid}`; else viewNotifications();
      if (S.unread > 0) S.unread--; break;
    }
    case "read-all":
      await api("/api/notifications/read-all", { method: "POST" }).catch(() => { });
      S.unread = 0; viewNotifications(); break;

    case "save-profile": {
      try {
        const d = await api("/api/profile", {
          method: "PUT", body: {
            name: val("#pf-name"), department: val("#pf-dept"), phone: val("#pf-phone"),
            current_password: $("#pf-cur").value, new_password: $("#pf-new").value,
          }
        });
        S.user = d.user; toast("Profile updated", $("#pf-new").value ? "Your password has been changed." : "");
        route();
      } catch (e) { apiError(e); } break;
    }

    case "admin-tab": S.adminTab = el.dataset.tab; location.hash = "#/admin/" + el.dataset.tab; viewAdmin(); break;
    case "user-new": userModal(null); break;
    case "user-edit": userModal(JSON.parse(el.dataset.u)); break;
    case "add-lookup": {
      const kind = el.dataset.kind, v = val("#lk-" + kind);
      if (!v) return;
      try { await api("/api/admin/lookups", { method: "POST", body: { kind, value: v } }); await loadLookups(); viewAdmin(); }
      catch (e) { apiError(e); } break;
    }
    case "del-lookup":
      try { await api("/api/admin/lookups/" + el.dataset.id, { method: "DELETE" }); await loadLookups(); viewAdmin(); }
      catch (e) { apiError(e); } break;
    case "mail-view": previewEmail(el.dataset.id); break;
    case "copy-mail": copyFormatted(el.dataset.mid); break;
    case "copy-html": {
      // the requester already has the rendered HTML from /messages — no admin call needed
      const m = (S.sendMessages || []).find(x => String(x.id) === el.dataset.mid);
      if (!m) return;
      const mode = await copyRich(m.body_html, m.body_text || "");
      if (mode === "rich") toast("Copied", "Paste into a new Outlook message — the design is preserved.");
      else if (mode === "plain") toast("Copied as text", "Rich copy isn't available here.");
      else toast("Copy blocked", "Your browser refused the copy.", "err");
      break;
    }
    case "admin-eml": {
      const { id, name } = el.dataset;
      const safe = (name || "message").replace(/[^\w\- ]+/g, "").slice(0, 60) || "message";
      try {
        await download(`/api/admin/emails/${id}/eml`, `${safe}.eml`);
      } catch (err) {
        toast("Could not open the draft", err.message, "bad");
      }
      return;
    }

    case "open-eml": {
      const { eid, mid, name } = el.dataset;
      const safe = (name || "message").replace(/[^\w\- ]+/g, "").slice(0, 60) || "message";
      try {
        await download(`/api/events/${eid}/messages/${mid}/eml`, `${safe}.eml`);
      } catch (err) {
        toast("Could not open the draft", err.message, "bad");
        return;
      }
      try {
        await api(`/api/events/${eid}/messages/${mid}/mark-sent`, { method: "POST" });
      } catch (err) { /* the draft is what matters; the marker is bookkeeping */ }
      toast("Draft downloaded", "Open it and press Send in Outlook.");
      route();
      return;
    }

    case "mailto-sent": {
      // the anchor still opens Outlook; we just record that it was handed over
      const { eid, mid } = el.dataset;
      setTimeout(async () => {
        const ok = await confirmDialog({
          title: "Did you send it?",
          message: "Mark this request as sent so the event history shows it left your mailbox. " +
            "If Outlook didn't open, choose Cancel and copy the message instead.",
          confirmLabel: "Yes, I sent it", tone: "good",
        });
        if (!ok) return;
        try {
          await api(`/api/events/${eid}/messages/${mid}/mark-sent`, { method: "POST" });
          toast("Recorded", "The approval history now shows you e-mailed the request.");
          route();
        } catch (e) { apiError(e); }
      }, 900);
      break;
    }
  }
});

document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeModal(); });

boot();
