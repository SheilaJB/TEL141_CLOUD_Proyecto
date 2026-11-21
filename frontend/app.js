const API = location.protocol.startsWith("http") ? "/api" : "https://localhost:8443";
const POLL_MS = 5000;

const S = {
    token: sessionStorage.getItem("token"),
    refresh: sessionStorage.getItem("refresh"),
    rol: sessionStorage.getItem("rol"),
    nivel: sessionStorage.getItem("nivel"),
    codigo: sessionStorage.getItem("codigo"),
    view: null,
    timer: null,
    cache: {},
};

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

function esc(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtDate(value) {
    if (!value) return "—";
    const d = new Date(value);
    return isNaN(d) ? esc(value) : d.toLocaleString("es-PE", { dateStyle: "short", timeStyle: "short" });
}

function fmtMb(mb) {
    if (mb == null) return "—";
    return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;
}

const BADGES = {
    RUNNING: "b-green", ACTIVE: "b-green", COMPLETED: "b-green", APPROVED: "b-green",
    DRAFT: "b-gray", ARCHIVED: "b-gray", ELIMINATED: "b-gray", DELETED: "b-gray", INACTIVE: "b-gray",
    PENDING: "b-yellow", VALIDATING: "b-yellow", APPROVAL_PENDING: "b-yellow",
    UPDATING: "b-blue", DEPLOYING: "b-blue", IN_PROGRESS: "b-blue",
    STOPPED: "b-orange", COMPENSATED: "b-orange", INCONSISTENT: "b-orange",
    FAILED: "b-red", REJECTED: "b-red", REJECTED_BY_OPERATOR: "b-red", REJECTED_BY_SYSTEM: "b-red", BLOCKED: "b-red",
};

function badge(value) {
    return `<span class="badge ${BADGES[value] || "b-purple"}">${esc(value || "—")}</span>`;
}

function bar(pct) {
    const p = Math.max(0, Math.min(100, Number(pct) || 0));
    const cls = p >= 90 ? "crit" : p >= 70 ? "warn" : "";
    return `<div>${p.toFixed(0)}%</div><div class="bar ${cls}"><span style="width:${p}%"></span></div>`;
}

function toast(message, type = "") {
    const el = document.createElement("div");
    el.className = `toast ${type}`;
    el.textContent = message;
    $("#toasts").appendChild(el);
    setTimeout(() => el.remove(), type === "err" ? 7000 : 4000);
}

function errorText(err) {
    const d = err && err.data;
    if (d) {
        if (d.message) return d.code ? `${d.code}: ${d.message}` : d.message;
        if (typeof d.detail === "string") return d.detail;
        if (Array.isArray(d.detail)) return d.detail.map(x => `${(x.loc || []).slice(1).join(".")}: ${x.msg}`).join(" · ");
    }
    return err && err.message ? err.message : "Error desconocido";
}

class ApiError extends Error {
    constructor(status, data) {
        super(`HTTP ${status}`);
        this.status = status;
        this.data = data;
    }
}

async function rawRequest(method, path, body, auth = true) {
    const headers = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (auth && S.token) headers.Authorization = `Bearer ${S.token}`;
    const res = await fetch(API + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const text = await res.text();
    let data = null;
    if (text) {
        try { data = JSON.parse(text); } catch { data = { detail: text }; }
    }
    if (!res.ok) throw new ApiError(res.status, data);
    return data;
}

async function tryRefresh() {
    if (!S.refresh) return false;
    try {
        const data = await rawRequest("POST", "/auth/refresh", { refresh_token: S.refresh }, false);
        S.token = data.access_token;
        if (data.refresh_token) S.refresh = data.refresh_token;
        sessionStorage.setItem("token", S.token);
        sessionStorage.setItem("refresh", S.refresh);
        return true;
    } catch {
        return false;
    }
}

async function api(method, path, body) {
    try {
        return await rawRequest(method, path, body);
    } catch (err) {
        if (err.status === 401 && await tryRefresh()) return rawRequest(method, path, body);
        if (err.status === 401) {
            logout();
            toast("Tu sesión expiró, vuelve a ingresar.", "err");
        }
        throw err;
    }
}

const get = path => api("GET", path);
const post = (path, body) => api("POST", path, body);

async function cached(key, path) {
    if (!S.cache[key]) S.cache[key] = await get(path);
    return S.cache[key];
}

function modal({ title, body, okText = "Aceptar", okClass = "primary", onOk }) {
    $("#modal-title").textContent = title;
    $("#modal-body").innerHTML = body;
    const ok = $("#modal-ok");
    ok.textContent = okText;
    ok.className = `btn ${okClass}`;
    $("#modal").classList.remove("hidden");
    const close = () => $("#modal").classList.add("hidden");
    $("#modal-cancel").onclick = close;
    ok.onclick = async () => {
        ok.disabled = true;
        try {
            if ((await onOk($("#modal-body"))) !== false) close();
        } catch (err) {
            toast(errorText(err), "err");
        } finally {
            ok.disabled = false;
        }
    };
}

function confirmModal(title, message, okText, okClass, action) {
    modal({ title, body: `<p>${esc(message)}</p>`, okText, okClass, onOk: action });
}

function loading(text = "Cargando...") {
    return `<div class="loading"><span class="spinner"></span>${esc(text)}</div>`;
}

function stopPolling() {
    if (S.timer) clearInterval(S.timer);
    S.timer = null;
}

function startPolling(fn) {
    stopPolling();
    S.timer = setInterval(fn, POLL_MS);
}

const NAV = {
    consumidor: [
        ["home", "Inicio"],
        ["slices", "Mis slices"],
        ["new", "Nuevo slice"],
        ["requests", "Mis solicitudes"],
    ],
    operador: [
        ["home", "Inicio"],
        ["approvals", "Aprobaciones"],
        ["slices", "Slices"],
        ["servers", "Servidores"],
        ["users", "Usuarios"],
        ["catalog", "Catálogo"],
    ],
    admin: [
        ["home", "Inicio"],
        ["approvals", "Aprobaciones"],
        ["slices", "Slices"],
        ["servers", "Servidores"],
        ["users", "Usuarios"],
        ["catalog", "Catálogo"],
    ],
};

function renderShell() {
    $("#login-view").classList.add("hidden");
    $("#app-view").classList.remove("hidden");
    $("#user-codigo").textContent = S.codigo || "";
    $("#user-rol").textContent = S.nivel && S.nivel !== "null" ? `${S.rol} · ${S.nivel}` : S.rol;
    $("#nav").innerHTML = (NAV[S.rol] || NAV.consumidor)
        .map(([id, label]) => `<button class="nav-item" data-view="${id}">${label}</button>`).join("");
    $$(".nav-item").forEach(b => b.onclick = () => go(b.dataset.view));
}

function go(view, arg) {
    stopPolling();
    S.view = view;
    $$(".nav-item").forEach(b => b.classList.toggle("active", b.dataset.view === view));
    const main = $("#main");
    main.innerHTML = loading();
    const views = {
        home: viewHome, slices: viewSlices, slice: viewSlice, new: viewEditor, edit: viewEditor,
        requests: viewRequests, approvals: viewApprovals, servers: viewServers, users: viewUsers, catalog: viewCatalog,
    };
    Promise.resolve((views[view] || viewHome)(main, arg)).catch(err => {
        main.innerHTML = `<div class="card"><h3>No se pudo cargar</h3><p class="error">${esc(errorText(err))}</p></div>`;
    });
}

async function login(e) {
    e.preventDefault();
    const btn = $("#login-btn");
    $("#login-error").textContent = "";
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span>';
    const codigo = $("#login-codigo").value.trim();
    try {
        const data = await rawRequest("POST", "/auth/login", { codigo, password: $("#login-password").value, client: "cli" }, false);
        Object.assign(S, { token: data.access_token, refresh: data.refresh_token, rol: data.rol, nivel: data.nivel, codigo, cache: {} });
        ["token", "refresh", "rol", "nivel", "codigo"].forEach(k => sessionStorage.setItem(k, S[k] ?? ""));
        renderShell();
        go("home");
    } catch (err) {
        const msgs = { 401: "Código o contraseña incorrectos.", 403: "Usuario inactivo o bloqueado.", 429: "Demasiados intentos, espera un minuto." };
        $("#login-error").textContent = msgs[err.status] || (err.status ? errorText(err) : "No hay conexión con el API Gateway.");
    } finally {
        btn.disabled = false;
        btn.textContent = "Ingresar";
    }
}

function logout() {
    stopPolling();
    if (S.refresh) rawRequest("POST", "/auth/logout", { refresh_token: S.refresh }, false).catch(() => {});
    sessionStorage.clear();
    Object.assign(S, { token: null, refresh: null, rol: null, nivel: null, codigo: null, cache: {} });
    $("#app-view").classList.add("hidden");
    $("#login-view").classList.remove("hidden");
    $("#login-form").reset();
}

function stat(label, value, sub = "") {
    return `<div class="stat"><div class="muted">${esc(label)}</div><div class="value">${esc(value)}</div>${sub ? `<div class="sub">${sub}</div>` : ""}</div>`;
}

async function viewHome(main) {
    if (S.rol === "consumidor") {
        const [quota, slices] = await Promise.all([get("/cruds/quota/me"), get("/cruds/slices")]);
        const running = slices.filter(s => s.estado === "RUNNING").length;
        main.innerHTML = `
            <div class="page-head"><div><h2>Hola, ${esc(S.codigo)}</h2><p class="muted">Nivel ${esc(quota.nivel || S.nivel)}</p></div>
                <button class="btn primary" id="go-new">+ Nuevo slice</button></div>
            <div class="grid cols-4">
                ${stat("Slices", slices.length, `${running} en ejecución`)}
                ${quota.cuotas.map(c => stat(c.recurso, c.disponible, `de ${c.prometido} · usados ${c.consumido_activos} · pendientes ${c.reservado_pendientes}`)).join("")}
            </div>
            <div class="card" style="margin-top:18px">
                <div class="card-head"><h3>Últimos slices</h3><button class="btn small" id="go-slices">Ver todos</button></div>
                ${slicesTable(slices.slice(0, 5))}
            </div>`;
        $("#go-new").onclick = () => go("new");
        $("#go-slices").onclick = () => go("slices");
        bindSliceLinks(main);
        return;
    }
    const [servers, pending] = await Promise.all([get("/cruds/servers"), get("/cruds/requests?estado=PENDING")]);
    let metrics = null;
    if (S.rol === "admin") metrics = await get("/cruds/metrics/overview").catch(() => null);
    const totalCpu = servers.reduce((a, s) => a + s.total_cpu_cores, 0);
    const totalRam = servers.reduce((a, s) => a + s.total_ram_mb, 0);
    main.innerHTML = `
        <div class="page-head"><div><h2>Panel de ${esc(S.rol)}</h2><p class="muted">Estado general de la plataforma</p></div></div>
        <div class="grid cols-4">
            ${metrics ? stat("Slices", metrics.total_slices, `${metrics.running_slices} en ejecución · ${metrics.draft_slices} borradores`) : ""}
            ${metrics ? stat("VMs", metrics.total_nodes, `${metrics.running_nodes} en ejecución`) : ""}
            ${stat("Aprobaciones pendientes", pending.length)}
            ${stat("Servidores activos", servers.filter(s => s.estado === "ACTIVE").length, `de ${servers.length}`)}
            ${stat("vCPU físicas", totalCpu, metrics ? `${metrics.total_vcpu_allocated} asignadas` : "")}
            ${stat("RAM física", fmtMb(totalRam), metrics ? `${fmtMb(metrics.total_vram_mb_allocated)} asignada` : "")}
        </div>
        <div class="card" style="margin-top:18px">
            <div class="card-head"><h3>Uso por servidor</h3><button class="btn small" id="go-servers">Detalle</button></div>
            ${serversTable(servers)}
        </div>`;
    $("#go-servers").onclick = () => go("servers");
}

function slicesTable(slices) {
    if (!slices.length) return `<div class="empty">No hay slices.</div>`;
    return `<div class="table-wrap"><table><thead><tr>
        <th>ID</th><th>Nombre</th><th>Estado</th><th>Zona</th><th>Versión activa</th><th>Modificado</th><th></th>
    </tr></thead><tbody>${slices.map(s => `<tr>
        <td>${s.id}</td><td><strong>${esc(s.nombre)}</strong></td><td>${badge(s.estado)}</td>
        <td>${esc(s.zona_nombre || s.zona_id || "—")}</td><td>${s.version_activa_numero ? `v${s.version_activa_numero}` : "—"}</td>
        <td>${fmtDate(s.fecha_modificacion)}</td>
        <td><button class="btn small" data-slice="${s.id}">Ver</button></td>
    </tr>`).join("")}</tbody></table></div>`;
}

function bindSliceLinks(root) {
    $$("[data-slice]", root).forEach(b => b.onclick = () => go("slice", Number(b.dataset.slice)));
}

async function viewSlices(main) {
    const filter = S.cache.sliceFilter || "";
    const slices = await get(`/cruds/slices${filter ? `?estado=${filter}` : ""}`);
    const states = ["", "DRAFT", "RUNNING", "UPDATING", "STOPPED", "FAILED"];
    main.innerHTML = `
        <div class="page-head"><div><h2>${S.rol === "consumidor" ? "Mis slices" : "Slices"}</h2><p class="muted">${slices.length} resultados</p></div>
            <div class="actions">${S.rol === "consumidor" ? '<button class="btn primary" id="go-new">+ Nuevo slice</button>' : ""}
            <button class="btn" id="reload">Actualizar</button></div></div>
        <div class="tabs">${states.map(s => `<button class="tab ${s === filter ? "active" : ""}" data-f="${s}">${s || "Todos"}</button>`).join("")}</div>
        <div class="card">${slicesTable(slices)}</div>`;
    $$(".tab", main).forEach(t => t.onclick = () => { S.cache.sliceFilter = t.dataset.f; go("slices"); });
    $("#reload").onclick = () => go("slices");
    if ($("#go-new")) $("#go-new").onclick = () => go("new");
    bindSliceLinks(main);
}

function liveNodes(topo) {
    return (topo.nodos || []).filter(n => n.estado_nodo !== "DELETED");
}

function ip(value) {
    return value ? String(value).replace(/\/32$/, "") : value;
}

function topologySvg(topo) {
    const nodes = liveNodes(topo);
    const specLinks = (topo.spec && topo.spec.links) || [];
    const links = specLinks.length
        ? specLinks.map(l => l.endpoints)
        : (topo.enlaces || []).map(e => (e.puertos || []).map(p => p.nodo_name));
    if (!nodes.length && !(topo.spec && topo.spec.vms && topo.spec.vms.length)) return `<div class="empty">Sin VMs.</div>`;
    const items = nodes.length ? nodes : topo.spec.vms.map(v => ({ name: v.name, estado_nodo: "DRAFT", imagen_nombre: v.image }));
    const w = 800, h = 320, cx = w / 2, cy = h / 2, rx = w / 2 - 90, ry = h / 2 - 70;
    const pos = {};
    items.forEach((n, i) => {
        const a = (2 * Math.PI * i) / items.length + Math.PI;
        pos[n.name] = items.length === 1 ? [cx, cy] : [cx + rx * Math.cos(a), cy + ry * Math.sin(a)];
    });
    const color = s => ({ RUNNING: "#3fb950", PENDING: "#d29922", FAILED: "#f85149", STOPPED: "#f0883e" }[s] || "#8b98a9");
    const lines = links.filter(e => e && e.length >= 2 && pos[e[0]] && pos[e[1]]).map(([a, b]) =>
        `<line x1="${pos[a][0]}" y1="${pos[a][1]}" x2="${pos[b][0]}" y2="${pos[b][1]}" stroke="#4c8dff" stroke-width="2" opacity=".7"/>`).join("");
    const publics = new Set(((topo.spec && topo.spec.vms) || []).filter(v => v.public).map(v => v.name));
    const circles = items.map(n => {
        const [x, y] = pos[n.name];
        return `<g>
            <circle cx="${x}" cy="${y}" r="24" fill="#161b22" stroke="${color(n.estado_nodo)}" stroke-width="3"/>
            ${publics.has(n.name) ? `<circle cx="${x + 18}" cy="${y - 18}" r="6" fill="#a371f7"><title>Pública</title></circle>` : ""}
            <text x="${x}" y="${y + 4}" text-anchor="middle">${esc(n.name)}</text>
            <text class="sub" x="${x}" y="${y + 40}" text-anchor="middle">${esc(ip(n.servidor_ip) || n.imagen_nombre || "")}</text>
        </g>`;
    }).join("");
    return `<svg class="topology" viewBox="0 0 ${w} ${h}" preserveAspectRatio="xMidYMid meet">${lines}${circles}</svg>`;
}

async function viewSlice(main, sliceId) {
    const render = async (first) => {
        const [summary, topo] = await Promise.all([get(`/cruds/slices/${sliceId}`), get(`/cruds/slices/${sliceId}/topology`)]);
        if (S.view !== "slice") return;
        S.cache.currentSlice = { summary, topo };
        const owner = S.rol === "consumidor";
        const isDraft = summary.estado === "DRAFT" && !summary.version_activa_numero;
        const canUpdate = owner && summary.estado === "RUNNING" && summary.version_activa_numero;
        const canDestroy = owner && ["DRAFT", "RUNNING", "STOPPED", "FAILED"].includes(summary.estado);
        const nodes = liveNodes(topo);
        const links = topo.enlaces || [];
        const out = first ? "" : ($("#slice-out") ? $("#slice-out").innerHTML : "");
        main.innerHTML = `
            <div class="page-head">
                <div><h2>${esc(summary.nombre)} ${badge(summary.estado)}</h2>
                    <p class="muted">Slice #${summary.id} · ${esc(summary.zona_nombre || "")} · versión ${topo.numero_version ? `v${topo.numero_version}` : "—"}</p></div>
                <div class="actions">
                    <button class="btn" id="back">← Volver</button>
                    ${owner && isDraft ? `<button class="btn" id="a-edit">Editar borrador</button>
                        <button class="btn" id="a-validate">Validar</button>
                        <button class="btn" id="a-plan">Ver plan</button>
                        <button class="btn primary" id="a-deploy">Desplegar</button>` : ""}
                    ${canUpdate ? `<button class="btn primary" id="a-update">Modificar</button>` : ""}
                    ${canDestroy ? `<button class="btn danger" id="a-destroy">Destruir</button>` : ""}
                </div>
            </div>
            <div class="grid cols-2">
                <div class="card"><h3>Datos</h3><dl class="kv">
                    <dt>Estado</dt><dd>${badge(summary.estado)}</dd>
                    <dt>Zona</dt><dd>${esc(summary.zona_nombre || summary.zona_id || "—")}</dd>
                    <dt>Versión activa</dt><dd>${summary.version_activa_numero ? `v${summary.version_activa_numero}` : "—"}</dd>
                    <dt>VLAN S-tag</dt><dd>${esc(summary.vlan_s ?? "—")}</dd>
                    <dt>Creado</dt><dd>${fmtDate(summary.fecha_creacion)}</dd>
                    <dt>Modificado</dt><dd>${fmtDate(summary.fecha_modificacion)}</dd>
                    <dt>VMs / enlaces</dt><dd>${nodes.length || (topo.spec?.vms || []).length} / ${links.length || (topo.spec?.links || []).length}</dd>
                </dl></div>
                <div class="card"><h3>Leyenda</h3><dl class="kv">
                    <dt>${badge("RUNNING")}</dt><dd>VM en ejecución</dd>
                    <dt>${badge("PENDING")}</dt><dd>VM creándose</dd>
                    <dt>${badge("FAILED")}</dt><dd>VM con error</dd>
                    <dt><span class="badge b-purple">●</span></dt><dd>VM con acceso público</dd>
                    <dt><span class="badge b-blue">—</span></dt><dd>Enlace privado</dd>
                </dl></div>
            </div>
            <div class="card"><h3>Topología</h3>${topologySvg(topo)}</div>
            <div class="card"><h3>Máquinas virtuales</h3>${nodes.length ? `<div class="table-wrap"><table><thead><tr>
                <th>Nombre</th><th>Estado</th><th>Flavor</th><th>Recursos</th><th>Imagen</th><th>Servidor</th><th>VNC</th>
            </tr></thead><tbody>${nodes.map(n => `<tr>
                <td><strong>${esc(n.name)}</strong></td><td>${badge(n.estado_nodo)}</td><td>${esc(n.flavor_name || "—")}</td>
                <td>${n.vcpu ?? "?"} vCPU · ${fmtMb(n.vram_mb)} · ${fmtMb(n.vdisk_mb)}</td>
                <td>${esc(n.imagen_nombre || "—")}</td><td>${esc(ip(n.servidor_ip) || n.servidor_id || "—")}</td><td>${esc(n.puerto_vnc ?? "—")}</td>
            </tr>`).join("")}</tbody></table></div>` : specVmsTable(topo.spec)}</div>
            <div class="card"><h3>Enlaces</h3>${links.length ? `<div class="table-wrap"><table><thead><tr>
                <th>Enlace</th><th>Puertos</th>
            </tr></thead><tbody>${links.map(l => `<tr><td><strong>${esc(l.name)}</strong></td><td>${(l.puertos || []).map(p =>
                `<div>${esc(p.nodo_name || p.nodo_id)} ${p.public ? badge("PUBLIC") : ""} <span class="muted">${esc(ip(p.ip) || "")} ${esc(p.mac || "")} ${p.vlan_tag != null ? `VLAN ${esc(p.vlan_tag)}` : ""}</span></div>`).join("")}</td></tr>`).join("")}
            </tbody></table></div>` : specLinksTable(topo.spec)}</div>
            <div class="card ${out ? "" : "hidden"}" id="slice-out-card"><div class="card-head"><h3>Resultado</h3><button class="btn small" id="close-out">Cerrar</button></div><div id="slice-out">${out}</div></div>`;
        $("#back").onclick = () => go("slices");
        $("#close-out").onclick = () => $("#slice-out-card").classList.add("hidden");
        const n = topo.numero_version;
        const showOut = html => { $("#slice-out").innerHTML = html; $("#slice-out-card").classList.remove("hidden"); };
        if ($("#a-edit")) $("#a-edit").onclick = () => go("edit", { mode: "draft", sliceId, version: n, spec: topo.spec, zonaId: summary.zona_id, name: summary.nombre });
        if ($("#a-validate")) $("#a-validate").onclick = async () => {
            try { showOut(validationHtml(await post(`/slices/${sliceId}/versions/${n}/validate`))); } catch (err) { toast(errorText(err), "err"); }
        };
        if ($("#a-plan")) $("#a-plan").onclick = async () => {
            try { showOut(planHtml(await post(`/slices/${sliceId}/versions/${n}/plan`))); } catch (err) { toast(errorText(err), "err"); }
        };
        if ($("#a-deploy")) $("#a-deploy").onclick = () => confirmModal("Desplegar slice", `Se desplegará la versión v${n} de "${summary.nombre}".`, "Desplegar", "primary", async () => {
            const d = await post(`/slices/${sliceId}/versions/${n}/deploy`);
            showOut(deploymentHtml(d));
            toast(d.approval_status === "PENDING" ? "Despliegue enviado: espera aprobación del operador." : "Despliegue iniciado.", "ok");
            render(false);
        });
        if ($("#a-update")) $("#a-update").onclick = () => go("edit", { mode: "update", sliceId, version: summary.version_activa_numero, spec: topo.spec, zonaId: summary.zona_id, name: summary.nombre });
        if ($("#a-destroy")) $("#a-destroy").onclick = () => confirmModal("Destruir slice", `Se eliminarán todas las VMs y redes de "${summary.nombre}". No se puede deshacer.`, "Destruir", "danger", async () => {
            const d = await post(`/slices/${sliceId}/destroy`);
            showOut(deploymentHtml(d));
            toast("Destrucción iniciada.", "ok");
            render(false);
        });
    };
    await render(true);
    startPolling(() => render(false).catch(() => {}));
}

function specVmsTable(spec) {
    const vms = (spec && spec.vms) || [];
    if (!vms.length) return `<div class="empty">Sin VMs.</div>`;
    return `<div class="table-wrap"><table><thead><tr><th>Nombre</th><th>Flavor</th><th>Imagen</th><th>Pública</th></tr></thead><tbody>
        ${vms.map(v => `<tr><td><strong>${esc(v.name)}</strong></td><td>${esc(v.flavor)}</td><td>${esc(v.image)}</td><td>${v.public ? "Sí" : "No"}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function specLinksTable(spec) {
    const links = (spec && spec.links) || [];
    if (!links.length) return `<div class="empty">Sin enlaces.</div>`;
    return `<div class="table-wrap"><table><thead><tr><th>Enlace</th><th>Extremos</th></tr></thead><tbody>
        ${links.map(l => `<tr><td><strong>${esc(l.name)}</strong></td><td>${(l.endpoints || []).map(esc).join(" ↔ ")}</td></tr>`).join("")}
    </tbody></table></div>`;
}

function deltaText(d) {
    return d ? `${d.vcpu} vCPU · ${fmtMb(d.ram_mb)} RAM · ${fmtMb(d.disk_mb)} disco` : "—";
}

function validationHtml(v) {
    return `<dl class="kv">
        <dt>Válido</dt><dd>${v.valid ? badge("APPROVED") : badge("REJECTED")}</dd>
        <dt>Capas</dt><dd>${(v.layers || []).map(esc).join(", ") || "—"}</dd>
        <dt>Recursos</dt><dd>${deltaText(v.resource_delta)}</dd>
        <dt>Requiere aprobación</dt><dd>${v.requires_approval ? "Sí" : "No"}</dd>
    </dl>`;
}

function planHtml(p) {
    const s = p.summary || {};
    return `<dl class="kv" style="margin-bottom:12px">
        <dt>Disrupción máxima</dt><dd>${esc(s.disruption_max || "—")}</dd>
        <dt>Destructivo</dt><dd>${s.destructive ? "Sí" : "No"}</dd>
        <dt>Recursos</dt><dd>${deltaText(s.resource_delta)}</dd>
        <dt>Requiere aprobación</dt><dd>${p.requires_approval ? "Sí" : "No"}</dd>
    </dl>
    ${(p.intents || []).length ? `<div class="table-wrap"><table><thead><tr><th>Acción</th><th>Tipo</th><th>Estrategia</th><th>Disrupción</th><th>Cambios</th></tr></thead><tbody>
        ${p.intents.map(i => `<tr><td>${badge(i.kind)}</td><td>${esc(i.target && i.target.table)}</td><td>${esc(i.strategy || "—")}</td><td>${esc(i.disruption || "—")}</td>
        <td>${(i.changes || []).map(c => `<div class="muted">${esc(c.field)}: ${esc(JSON.stringify(c.before))} → ${esc(JSON.stringify(c.after))}</div>`).join("")}</td></tr>`).join("")}
    </tbody></table></div>` : `<div class="empty">Sin cambios.</div>`}`;
}

function deploymentHtml(d) {
    return `<dl class="kv">
        <dt>Deployment</dt><dd>#${esc(d.deployment_id)}</dd>
        <dt>Estado</dt><dd>${badge(d.state)}</dd>
        <dt>Aprobación</dt><dd>${badge(d.approval_status)}</dd>
        ${d.rejection_reason ? `<dt>Motivo</dt><dd>${esc(d.rejection_reason)}</dd>` : ""}
    </dl>`;
}

async function viewEditor(main, ctx) {
    const mode = (ctx && ctx.mode) || "create";
    const [zones, flavors, images] = await Promise.all([
        cached("zones", "/cruds/zones"), cached("flavors", "/cruds/flavors"), cached("images", "/cruds/images"),
    ]);
    const spec = JSON.parse(JSON.stringify((ctx && ctx.spec) || { schema_version: 1, vms: [], links: [] }));
    spec.vms = spec.vms || [];
    spec.links = spec.links || [];
    if (!spec.vms.length) spec.vms.push({ name: "vm1", flavor: (flavors[0] || {}).name || "", image: "", public: false });
    const titles = { create: "Nuevo slice", draft: "Editar borrador", update: "Modificar slice en ejecución" };
    const zoneId = () => Number((ctx && ctx.zonaId) || ($("#f-zone") && $("#f-zone").value) || (zones[0] || {}).id);
    const clusterOf = id => (zones.find(z => z.id === id) || {}).cluster_id;
    const imagesFor = () => images.filter(i => i.cluster_compatible == null || i.cluster_compatible === clusterOf(zoneId()));

    main.innerHTML = `
        <div class="page-head"><div><h2>${titles[mode]}</h2>
            <p class="muted">${mode === "create" ? "Define las VMs y enlaces del slice." : `${esc(ctx.name)} · ${mode === "update" ? `nueva versión a partir de v${ctx.version}` : `versión v${ctx.version}`}`}</p></div>
            <div class="actions"><button class="btn" id="back">Cancelar</button></div></div>
        ${mode === "create" ? `<div class="card"><div class="grid form-3">
            <label>Nombre del slice<input id="f-name" placeholder="mi-slice" maxlength="200"></label>
            <label>Zona<select id="f-zone">${zones.filter(z => z.estado === "ACTIVE").map(z => `<option value="${z.id}">${esc(z.nombre)}${z.cluster_id === 2 ? " (OpenStack)" : z.cluster_id === 1 ? " (Linux)" : ""}</option>`).join("")}</select></label>
        </div></div>` : ""}
        <div class="tabs"><button class="tab active" data-t="form">Formulario</button><button class="tab" data-t="json">JSON</button></div>
        <div id="pane-form">
            <div class="card"><div class="card-head"><h3>Máquinas virtuales</h3><button class="btn small" id="add-vm">+ VM</button></div>
                <div class="row-editor" id="vms"></div></div>
            <div class="card"><div class="card-head"><h3>Enlaces</h3><button class="btn small" id="add-link">+ Enlace</button></div>
                <div class="row-editor" id="links"></div></div>
            <div class="card"><h3>Vista previa</h3><div id="preview"></div></div>
        </div>
        <div id="pane-json" class="card hidden"><textarea id="f-json"></textarea><p class="muted">Formato: schema_version, vms[name, flavor, image, public], links[name, endpoints].</p></div>
        <div class="actions" style="justify-content:flex-end">
            ${mode === "create" ? `<label class="check"><input type="checkbox" id="f-deploy" checked> Desplegar al crear</label>` : ""}
            <button class="btn primary" id="save">${mode === "create" ? "Crear slice" : mode === "draft" ? "Guardar borrador" : "Aplicar cambios"}</button>
        </div>`;

    const options = (list, value, key, label) => list.map(x => `<option value="${esc(x[key])}" ${x[key] === value ? "selected" : ""}>${esc(label(x))}</option>`).join("");
    const draw = () => {
        const imgs = imagesFor();
        $("#vms").innerHTML = spec.vms.map((v, i) => `<div class="row">
            <input data-i="${i}" data-k="name" value="${esc(v.name)}" placeholder="nombre">
            <select data-i="${i}" data-k="flavor">${options(flavors, v.flavor, "name", f => `${f.name} · ${f.vcpu}c ${f.vram_mb}MB ${f.vdisk_mb}MB`)}</select>
            <select data-i="${i}" data-k="image"><option value="">Imagen...</option>${options(imgs, v.image, "nombre", m => m.nombre)}</select>
            <label class="check"><input type="checkbox" data-i="${i}" data-k="public" ${v.public ? "checked" : ""}> Pública</label>
            <button class="btn small danger" data-del-vm="${i}">✕</button>
        </div>`).join("") || `<div class="empty">Agrega al menos una VM.</div>`;
        const names = spec.vms.map(v => v.name).filter(Boolean);
        $("#links").innerHTML = spec.links.map((l, i) => `<div class="row link">
            <input data-l="${i}" data-k="name" value="${esc(l.name)}" placeholder="nombre">
            <select data-l="${i}" data-k="a">${names.map(n => `<option ${l.endpoints[0] === n ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
            <select data-l="${i}" data-k="b">${names.map(n => `<option ${l.endpoints[1] === n ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
            <button class="btn small danger" data-del-link="${i}">✕</button>
        </div>`).join("") || `<div class="empty">Sin enlaces.</div>`;
        $("#preview").innerHTML = topologySvg({ spec, nodos: [] });
        $$("#vms [data-k]").forEach(el => el.onchange = el.oninput = ev => {
            const v = spec.vms[el.dataset.i];
            const old = v.name;
            v[el.dataset.k] = el.type === "checkbox" ? el.checked : el.value.trim();
            if (el.dataset.k === "name") {
                spec.links.forEach(l => l.endpoints = l.endpoints.map(e => e === old ? v.name : e));
                if (ev.type === "change") draw();
            }
            if (el.dataset.k === "public") $("#preview").innerHTML = topologySvg({ spec, nodos: [] });
        });
        $$("#links [data-k]").forEach(el => el.onchange = el.oninput = () => {
            const l = spec.links[el.dataset.l];
            if (el.dataset.k === "name") l.name = el.value.trim();
            if (el.dataset.k === "a") l.endpoints[0] = el.value;
            if (el.dataset.k === "b") l.endpoints[1] = el.value;
            $("#preview").innerHTML = topologySvg({ spec, nodos: [] });
        });
        $$("[data-del-vm]").forEach(b => b.onclick = () => {
            const name = spec.vms[b.dataset.delVm].name;
            spec.vms.splice(b.dataset.delVm, 1);
            spec.links = spec.links.filter(l => !l.endpoints.includes(name));
            draw();
        });
        $$("[data-del-link]").forEach(b => b.onclick = () => { spec.links.splice(b.dataset.delLink, 1); draw(); });
    };
    draw();

    $("#back").onclick = () => ctx && ctx.sliceId ? go("slice", ctx.sliceId) : go("slices");
    if ($("#f-zone")) $("#f-zone").onchange = () => {
        const valid = new Set(imagesFor().map(i => i.nombre));
        spec.vms.forEach(v => { if (!valid.has(v.image)) v.image = ""; });
        draw();
    };
    $("#add-vm").onclick = () => {
        let k = spec.vms.length + 1;
        while (spec.vms.some(v => v.name === `vm${k}`)) k++;
        spec.vms.push({ name: `vm${k}`, flavor: (flavors[0] || {}).name || "", image: "", public: false });
        draw();
    };
    $("#add-link").onclick = () => {
        if (spec.vms.length < 2) return toast("Necesitas al menos 2 VMs para un enlace.", "err");
        const [a, b] = [spec.vms[0].name, spec.vms[1].name];
        spec.links.push({ name: `l-${a}-${b}-${spec.links.length + 1}`, endpoints: [a, b] });
        draw();
    };
    $$(".tab", main).forEach(t => t.onclick = () => {
        $$(".tab", main).forEach(x => x.classList.toggle("active", x === t));
        if (t.dataset.t === "json") {
            $("#f-json").value = JSON.stringify(spec, null, 2);
        } else {
            try {
                const parsed = JSON.parse($("#f-json").value);
                spec.vms = parsed.vms || [];
                spec.links = parsed.links || [];
                draw();
            } catch {
                toast("JSON inválido, se mantienen los datos del formulario.", "err");
            }
        }
        $("#pane-form").classList.toggle("hidden", t.dataset.t !== "form");
        $("#pane-json").classList.toggle("hidden", t.dataset.t !== "json");
    });

    $("#save").onclick = async () => {
        let body = spec;
        if (!$("#pane-json").classList.contains("hidden")) {
            try { body = JSON.parse($("#f-json").value); } catch { return toast("JSON inválido.", "err"); }
        }
        body = {
            schema_version: 1,
            vms: (body.vms || []).map(v => ({ name: v.name, flavor: v.flavor, image: v.image, public: !!v.public })),
            links: (body.links || []).map(l => ({ name: l.name, endpoints: l.endpoints })),
        };
        if (!body.vms.length) return toast("Agrega al menos una VM.", "err");
        if (body.vms.some(v => !v.image)) return toast("Todas las VMs necesitan una imagen.", "err");
        if (body.links.some(l => l.endpoints[0] === l.endpoints[1])) return toast("Un enlace no puede unir una VM consigo misma.", "err");
        const btn = $("#save");
        btn.disabled = true;
        try {
            if (mode === "create") {
                const name = $("#f-name").value.trim();
                if (!name) { btn.disabled = false; return toast("Ponle un nombre al slice.", "err"); }
                const created = await post("/slices", { name, zona_id: Number($("#f-zone").value), spec: body });
                toast(`Slice #${created.slice_id} creado (${created.cluster}).`, "ok");
                if ($("#f-deploy").checked) {
                    const d = await post(`/slices/${created.slice_id}/versions/${created.numero_version}/deploy`);
                    toast(d.approval_status === "PENDING" ? "Despliegue enviado: espera aprobación." : "Despliegue iniciado.", "ok");
                }
                go("slice", created.slice_id);
            } else if (mode === "draft") {
                await api("PUT", `/slices/${ctx.sliceId}/versions/${ctx.version}`, body);
                toast("Borrador guardado.", "ok");
                go("slice", ctx.sliceId);
            } else {
                const v = await post(`/slices/${ctx.sliceId}/versions`, { from_version: ctx.version });
                const d = await post(`/slices/${ctx.sliceId}/versions/${v.numero_version}/update`, body);
                toast(d.approval_status === "PENDING" ? "Cambios enviados: espera aprobación." : "Actualización iniciada.", "ok");
                go("slice", ctx.sliceId);
            }
        } catch (err) {
            toast(errorText(err), "err");
            btn.disabled = false;
        }
    };
}

function requestsTable(rows, withActions) {
    if (!rows.length) return `<div class="empty">No hay solicitudes.</div>`;
    return `<div class="table-wrap"><table><thead><tr>
        <th>#</th><th>Slice</th><th>Usuario</th><th>Tipo</th><th>Estado</th><th>Fecha</th><th>Motivo</th>${withActions ? "<th></th>" : ""}
    </tr></thead><tbody>${rows.map(r => `<tr>
        <td>${r.id}</td><td><a href="#" data-slice-link="${r.slice_id}">${esc(r.slice_nombre || r.slice_id)}</a></td>
        <td>${esc(r.usuario_codigo || r.usuario_id)}</td><td>${badge(r.tipo_deployment)}</td><td>${badge(r.estado)}</td>
        <td>${fmtDate(r.fecha_sol)}</td><td>${esc(r.motivo || "—")}</td>
        ${withActions ? `<td><div class="actions">${r.estado === "PENDING" ? `
            <button class="btn small success" data-approve="${r.deployment_id}">Aprobar</button>
            <button class="btn small danger" data-reject="${r.deployment_id}">Rechazar</button>` : ""}</div></td>` : ""}
    </tr>`).join("")}</tbody></table></div>`;
}

function bindRequestLinks(root) {
    $$("[data-slice-link]", root).forEach(a => a.onclick = e => { e.preventDefault(); go("slice", Number(a.dataset.sliceLink)); });
}

async function viewRequests(main) {
    const rows = await get("/cruds/requests");
    main.innerHTML = `<div class="page-head"><div><h2>Mis solicitudes</h2><p class="muted">Despliegues que requieren aprobación</p></div>
        <button class="btn" id="reload">Actualizar</button></div>
        <div class="card">${requestsTable(rows, false)}</div>`;
    $("#reload").onclick = () => go("requests");
    bindRequestLinks(main);
}

async function viewApprovals(main) {
    const filter = S.cache.approvalFilter ?? "PENDING";
    const rows = await get(`/cruds/requests${filter ? `?estado=${filter}` : ""}`);
    const states = ["PENDING", "APPROVED", "REJECTED_BY_OPERATOR", "REJECTED_BY_SYSTEM", ""];
    main.innerHTML = `<div class="page-head"><div><h2>Aprobaciones</h2><p class="muted">Solicitudes de despliegue de los consumidores</p></div>
        <button class="btn" id="reload">Actualizar</button></div>
        <div class="tabs">${states.map(s => `<button class="tab ${s === filter ? "active" : ""}" data-f="${s}">${s || "Todas"}</button>`).join("")}</div>
        <div class="card">${requestsTable(rows, true)}</div>`;
    $$(".tab", main).forEach(t => t.onclick = () => { S.cache.approvalFilter = t.dataset.f; go("approvals"); });
    $("#reload").onclick = () => go("approvals");
    bindRequestLinks(main);
    const decide = (id, approved) => modal({
        title: approved ? "Aprobar despliegue" : "Rechazar despliegue",
        body: `<label>Motivo ${approved ? "(opcional)" : ""}<textarea id="m-reason" style="min-height:90px" maxlength="2000"></textarea></label>`,
        okText: approved ? "Aprobar" : "Rechazar",
        okClass: approved ? "primary" : "danger",
        onOk: async body => {
            const reason = $("#m-reason", body).value.trim();
            if (!approved && !reason) { toast("Indica el motivo del rechazo.", "err"); return false; }
            await post(`/slices/deployments/${id}/approve`, reason ? { approved, reason } : { approved });
            toast(approved ? "Despliegue aprobado." : "Despliegue rechazado.", "ok");
            go("approvals");
        },
    });
    $$("[data-approve]", main).forEach(b => b.onclick = () => decide(b.dataset.approve, true));
    $$("[data-reject]", main).forEach(b => b.onclick = () => decide(b.dataset.reject, false));
    startPolling(async () => {
        const fresh = await get(`/cruds/requests${filter ? `?estado=${filter}` : ""}`).catch(() => null);
        if (fresh && fresh.length !== rows.length && S.view === "approvals") go("approvals");
    });
}

function serversTable(servers) {
    if (!servers.length) return `<div class="empty">No hay servidores.</div>`;
    return `<div class="table-wrap"><table><thead><tr>
        <th>ID</th><th>IP</th><th>Zona</th><th>Estado</th><th>CPU</th><th>RAM</th><th>Disco</th>
    </tr></thead><tbody>${servers.map(s => `<tr>
        <td>${s.id}</td><td>${esc(s.ip_serv)}</td><td>${esc(s.zona_nombre || s.zona_id || "—")}</td><td>${badge(s.estado)}</td>
        <td>${bar(s.cpu_usage_pct)}<div class="muted">${s.vcpu_reserved}/${s.total_cpu_cores} vCPU</div></td>
        <td>${bar(s.ram_usage_pct)}<div class="muted">${fmtMb(s.vram_mb_reserved)}/${fmtMb(s.total_ram_mb)}</div></td>
        <td>${bar(s.disk_usage_pct)}<div class="muted">${fmtMb(s.vdisk_mb_reserved)}/${fmtMb(s.total_disk_mb)}</div></td>
    </tr>`).join("")}</tbody></table></div>`;
}

async function viewServers(main) {
    const [servers, zones] = await Promise.all([get("/cruds/servers"), cached("zones", "/cruds/zones")]);
    main.innerHTML = `<div class="page-head"><div><h2>Servidores</h2><p class="muted">Capacidad y reservas por worker</p></div>
        <button class="btn" id="reload">Actualizar</button></div>
        ${zones.map(z => {
            const list = servers.filter(s => s.zona_id === z.id);
            return `<div class="card"><div class="card-head"><h3>${esc(z.nombre)} ${badge(z.estado)}</h3>
                <span class="muted">${z.cluster_id === 2 ? "OpenStack" : z.cluster_id === 1 ? "Linux" : ""} · CPU x${z.cpu_allocation_ratio_default} (máx x${z.cpu_allocation_ratio_limit}) · RAM x${z.ram_allocation_ratio_default}</span></div>
                ${serversTable(list)}</div>`;
        }).join("")}`;
    $("#reload").onclick = () => go("servers");
    startPolling(async () => {
        if (S.view !== "servers") return;
        const fresh = await get("/cruds/servers").catch(() => null);
        if (fresh && JSON.stringify(fresh) !== JSON.stringify(servers)) go("servers");
    });
}

async function viewUsers(main) {
    const users = await get("/cruds/users");
    const admin = S.rol === "admin";
    main.innerHTML = `<div class="page-head"><div><h2>Usuarios</h2><p class="muted">${users.length} usuarios</p></div>
        <div class="actions">${admin ? '<button class="btn primary" id="new-user">+ Nuevo usuario</button>' : ""}<button class="btn" id="reload">Actualizar</button></div></div>
        <div class="card"><div class="table-wrap"><table><thead><tr>
            <th>ID</th><th>Código</th><th>Rol</th><th>Nivel</th><th>Estado</th><th>Creado</th>${admin ? "<th></th>" : ""}
        </tr></thead><tbody>${users.map(u => `<tr>
            <td>${u.id}</td><td><strong>${esc(u.codigo)}</strong></td><td>${esc(u.rol)}</td><td>${esc(u.nivel || "—")}</td>
            <td>${badge(u.estado)}</td><td>${fmtDate(u.fecha_creacion)}</td>
            ${admin ? `<td><button class="btn small" data-edit-user="${u.id}">Editar</button></td>` : ""}
        </tr>`).join("")}</tbody></table></div></div>`;
    $("#reload").onclick = () => go("users");
    if (!admin) return;
    const roleFields = (u = {}) => `
        <label>Rol<select id="m-rol">${["consumidor", "operador", "admin"].map(r => `<option ${u.rol === r ? "selected" : ""}>${r}</option>`).join("")}</select></label>
        <label id="m-nivel-wrap">Nivel<select id="m-nivel">${["basico", "avanzado"].map(n => `<option ${u.nivel === n ? "selected" : ""}>${n}</option>`).join("")}</select></label>`;
    const syncNivel = body => {
        const toggle = () => $("#m-nivel-wrap", body).classList.toggle("hidden", $("#m-rol", body).value !== "consumidor");
        $("#m-rol", body).onchange = toggle;
        toggle();
    };
    $("#new-user").onclick = () => {
        modal({
            title: "Nuevo usuario",
            body: `<label>Código<input id="m-codigo"></label><label>Contraseña<input id="m-pass" type="password" minlength="6"></label>${roleFields({ rol: "consumidor", nivel: "basico" })}`,
            okText: "Crear",
            onOk: async body => {
                const rol = $("#m-rol", body).value;
                const data = { codigo: $("#m-codigo", body).value.trim(), password: $("#m-pass", body).value, rol };
                if (rol === "consumidor") data.nivel = $("#m-nivel", body).value;
                if (!data.codigo || data.password.length < 6) { toast("Código obligatorio y contraseña de mínimo 6 caracteres.", "err"); return false; }
                await post("/auth/usuarios", data);
                toast("Usuario creado.", "ok");
                go("users");
            },
        });
        syncNivel($("#modal-body"));
    };
    $$("[data-edit-user]", main).forEach(b => b.onclick = () => {
        const u = users.find(x => x.id === Number(b.dataset.editUser));
        modal({
            title: `Editar ${u.codigo}`,
            body: `${roleFields(u)}
                <label>Estado<select id="m-estado">${["ACTIVE", "INACTIVE", "BLOCKED"].map(s => `<option ${u.estado === s ? "selected" : ""}>${s}</option>`).join("")}</select></label>
                <label>Nueva contraseña (opcional)<input id="m-pass" type="password" minlength="6"></label>`,
            okText: "Guardar",
            onOk: async body => {
                const rol = $("#m-rol", body).value;
                const data = { rol, estado: $("#m-estado", body).value };
                data.nivel = rol === "consumidor" ? $("#m-nivel", body).value : null;
                const pass = $("#m-pass", body).value;
                if (pass) {
                    if (pass.length < 6) { toast("La contraseña debe tener mínimo 6 caracteres.", "err"); return false; }
                    data.password = pass;
                }
                await api("PATCH", `/auth/usuarios/${u.id}`, data);
                toast("Usuario actualizado.", "ok");
                go("users");
            },
        });
        syncNivel($("#modal-body"));
    });
}

async function viewCatalog(main) {
    const [zones, flavors, images] = await Promise.all([get("/cruds/zones"), get("/cruds/flavors?active_only=false"), get("/cruds/images?active_only=false")]);
    const cluster = id => id === 1 ? "Linux" : id === 2 ? "OpenStack" : "Todos";
    main.innerHTML = `<div class="page-head"><div><h2>Catálogo</h2><p class="muted">Zonas, flavors e imágenes disponibles</p></div></div>
        <div class="card"><h3>Zonas de disponibilidad</h3><div class="table-wrap"><table><thead><tr>
            <th>ID</th><th>Nombre</th><th>Cluster</th><th>Estado</th><th>CPU (def/máx)</th><th>RAM (def/máx)</th><th>Disco (def/máx)</th>
        </tr></thead><tbody>${zones.map(z => `<tr><td>${z.id}</td><td><strong>${esc(z.nombre)}</strong></td><td>${cluster(z.cluster_id)}</td><td>${badge(z.estado)}</td>
            <td>x${z.cpu_allocation_ratio_default} / x${z.cpu_allocation_ratio_limit}</td><td>x${z.ram_allocation_ratio_default} / x${z.ram_allocation_ratio_limit}</td>
            <td>x${z.disk_allocation_ratio_default} / x${z.disk_allocation_ratio_limit}</td></tr>`).join("")}</tbody></table></div></div>
        <div class="grid cols-2">
            <div class="card"><h3>Flavors</h3><div class="table-wrap"><table><thead><tr><th>Nombre</th><th>vCPU</th><th>RAM</th><th>Disco</th><th>Activo</th></tr></thead><tbody>
                ${flavors.map(f => `<tr><td><strong>${esc(f.name)}</strong><div class="muted">${esc(f.descripcion || "")}</div></td><td>${f.vcpu}</td><td>${fmtMb(f.vram_mb)}</td><td>${fmtMb(f.vdisk_mb)}</td><td>${f.activo ? "Sí" : "No"}</td></tr>`).join("")}
            </tbody></table></div></div>
            <div class="card"><h3>Imágenes</h3><div class="table-wrap"><table><thead><tr><th>Nombre</th><th>Cluster</th><th>Referencia</th><th>cloud-init</th><th>Estado</th></tr></thead><tbody>
                ${images.map(i => `<tr><td><strong>${esc(i.nombre)}</strong></td><td>${cluster(i.cluster_compatible)}</td><td class="muted">${esc(i.ruta_referencia)}</td><td>${i.cloud_init ? "Sí" : "No"}</td><td>${badge(i.estado)}</td></tr>`).join("")}
            </tbody></table></div></div>
        </div>`;
}

$("#login-form").addEventListener("submit", login);
$("#logout-btn").onclick = logout;
$("#modal").addEventListener("click", e => { if (e.target.id === "modal") $("#modal").classList.add("hidden"); });

if (S.token) {
    renderShell();
    go("home");
}
