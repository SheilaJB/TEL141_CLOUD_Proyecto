/**
 * app.js — Controlador Principal de la Interfaz Gráfica de Usuario
 * Orquestador Cloud TEL141 - Grupo 3
 */

import { API, Storage } from "./api.js";
import { Quotas } from "./quotas.js";
import { Drafts } from "./drafts.js";
import { Slices } from "./slices.js";
import { Requests } from "./requests.js";
import { Wizard } from "./wizard.js";
import { AdminView } from "./admin.js";

let activeTab = "drafts"; // "drafts" | "requests" | "slices"
let autoPollingInterval = null;

// Inicialización de la aplicación
document.addEventListener("DOMContentLoaded", () => {
    initAuth();
    initTabs();
    initGlobalButtons();

    // Eventos personalizados
    window.addEventListener("auth:expired", () => showLoginView());
    window.addEventListener("slice:deployed", () => {
        switchTab("requests");
        startSmartPolling();
    });
    window.addEventListener("draft:created", () => {
        switchTab("drafts");
    });
    window.addEventListener("slice:new_version", () => {
        switchTab("drafts");
    });
});

function initAuth() {
    const token = Storage.getToken();
    const user = Storage.getUser();

    if (token && user) {
        showDashboardView(user);
    } else {
        showLoginView();
    }

    // Formulario de Login
    const loginForm = document.getElementById("login-form");
    if (loginForm) {
        loginForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            const btn = document.getElementById("btn-login");
            const errBox = document.getElementById("login-error");
            errBox.classList.add("hidden");
            btn.disabled = true;
            btn.innerText = "VERIFICANDO...";

            const codigo = document.getElementById("login-codigo").value.trim();
            const password = document.getElementById("login-password").value;

            try {
                const res = await API.auth.login(codigo, password);
                const userInfo = {
                    codigo,
                    rol: res.rol,
                    nivel: res.nivel
                };
                Storage.setToken(res.access_token);
                Storage.setUser(userInfo);
                showDashboardView(userInfo);
            } catch (err) {
                errBox.innerText = err.message;
                errBox.classList.remove("hidden");
            } finally {
                btn.disabled = false;
                btn.innerText = "INGRESAR";
            }
        });
    }

    // Botón Salir
    const btnLogout = document.getElementById("btn-logout");
    if (btnLogout) {
        btnLogout.addEventListener("click", async () => {
            if (!confirm("¿Deseas cerrar tu sesión?")) return;
            stopSmartPolling();
            await API.auth.logout();
            showLoginView();
        });
    }
}

function showLoginView() {
    document.getElementById("view-login").classList.remove("hidden");
    document.getElementById("view-dashboard").classList.add("hidden");
    stopSmartPolling();
}

function showDashboardView(user) {
    document.getElementById("view-login").classList.add("hidden");
    document.getElementById("view-dashboard").classList.remove("hidden");

    // Identidad en Navbar
    document.getElementById("nav-user-codigo").innerText = user.codigo || "Usuario";
    const badge = document.getElementById("nav-user-nivel");

    const isConsumer = user.rol === "consumidor";
    const isOperator = user.rol === "operador";
    const isAdmin = user.rol === "admin";

    // 1. Personalización de Barra Superior
    if (isOperator) {
        badge.innerText = "OPERADOR DE PLATAFORMA";
        badge.className = "text-xs font-bold px-2.5 py-0.5 rounded-full bg-amber-100 text-amber-900 border border-amber-300";
    } else if (isAdmin) {
        badge.innerText = "ADMINISTRADOR";
        badge.className = "text-xs font-bold px-2.5 py-0.5 rounded-full bg-slate-900 text-white";
    } else {
        if (user.nivel === "avanzado") {
            badge.innerText = "NIVEL AVANZADO";
            badge.className = "text-xs font-bold px-2 py-0.5 rounded-full bg-purple-100 text-purple-800 border border-purple-300";
        } else {
            badge.innerText = "NIVEL BÁSICO";
            badge.className = "text-xs font-bold px-2 py-0.5 rounded-full bg-blue-100 text-blue-800 border border-blue-300";
        }
    }

    // 2. Paneles Superiores Condicionales (Cuotas para Consumidor vs Resumen Operativo para Operador)
    const secQuotas = document.getElementById("section-quotas");
    const secOpSummary = document.getElementById("section-operator-summary");

    if (isConsumer) {
        if (secQuotas) secQuotas.classList.remove("hidden");
        if (secOpSummary) secOpSummary.classList.add("hidden");
    } else if (isOperator) {
        if (secQuotas) secQuotas.classList.add("hidden");
        if (secOpSummary) secOpSummary.classList.remove("hidden");
    } else {
        if (secQuotas) secQuotas.classList.add("hidden");
        if (secOpSummary) secOpSummary.classList.add("hidden");
    }

    // 3. Pestañas estrictamente filtradas según el Rol
    const btnDrafts = document.getElementById("tab-btn-drafts");
    const btnRequests = document.getElementById("tab-btn-requests");
    const btnSlices = document.getElementById("tab-btn-slices");
    const btnInfra = document.getElementById("tab-btn-infra");

    const btnAdminDash = document.getElementById("tab-btn-admin-dashboard");
    const btnAdminInfra = document.getElementById("tab-btn-admin-infra");
    const btnAdminUsers = document.getElementById("tab-btn-admin-users");
    const btnAdminQuotas = document.getElementById("tab-btn-admin-quotas");
    const btnAdminAudit = document.getElementById("tab-btn-admin-audit");

    if (isAdmin) {
        // ADMINISTRADOR DE INFRAESTRUCTURA:
        // Ocultar pestañas de usuario y operador (no participa en aprobación rutinaria ni borradores)
        if (btnDrafts) btnDrafts.classList.add("hidden");
        if (btnRequests) btnRequests.classList.add("hidden");
        if (btnSlices) btnSlices.classList.add("hidden");
        if (btnInfra) btnInfra.classList.add("hidden");

        // Mostrar pestañas del Administrador
        if (btnAdminDash) btnAdminDash.classList.remove("hidden");
        if (btnAdminInfra) btnAdminInfra.classList.remove("hidden");
        if (btnAdminUsers) btnAdminUsers.classList.remove("hidden");
        if (btnAdminQuotas) btnAdminQuotas.classList.remove("hidden");
        if (btnAdminAudit) btnAdminAudit.classList.remove("hidden");

        switchTab("admin-dashboard");
    } else if (isOperator) {
        // OPERADOR: NO crea borradores, NO ve catálogo de recursos ni infraestructura
        if (btnDrafts) btnDrafts.classList.add("hidden");
        if (btnInfra) btnInfra.classList.add("hidden");
        if (btnAdminDash) btnAdminDash.classList.add("hidden");
        if (btnAdminInfra) btnAdminInfra.classList.add("hidden");
        if (btnAdminUsers) btnAdminUsers.classList.add("hidden");
        if (btnAdminQuotas) btnAdminQuotas.classList.add("hidden");
        if (btnAdminAudit) btnAdminAudit.classList.add("hidden");

        if (btnRequests) {
            btnRequests.classList.remove("hidden");
            btnRequests.innerText = "1. BANDEJA DE SOLICITUDES";
        }
        if (btnSlices) {
            btnSlices.classList.remove("hidden");
            btnSlices.innerText = "2. SLICES DESPLEGADOS (ACTIVOS)";
        }
        switchTab("requests");
    } else {
        // CONSUMIDOR: Borradores, Solicitudes, Slices Activos y Catálogo disponible
        if (btnAdminDash) btnAdminDash.classList.add("hidden");
        if (btnAdminInfra) btnAdminInfra.classList.add("hidden");
        if (btnAdminUsers) btnAdminUsers.classList.add("hidden");
        if (btnAdminQuotas) btnAdminQuotas.classList.add("hidden");
        if (btnAdminAudit) btnAdminAudit.classList.add("hidden");

        if (btnDrafts) {
            btnDrafts.classList.remove("hidden");
            btnDrafts.innerText = "1. MIS BORRADORES";
        }
        if (btnRequests) {
            btnRequests.classList.remove("hidden");
            btnRequests.innerText = "2. MIS SOLICITUDES";
        }
        if (btnSlices) {
            btnSlices.classList.remove("hidden");
            btnSlices.innerText = "3. SLICES ACTIVOS";
        }
        if (btnInfra) {
            btnInfra.classList.remove("hidden");
            btnInfra.innerText = "4. CATÁLOGO DISPONIBLE";
        }
        switchTab("drafts");
    }

    refreshAllData();
}

function initTabs() {
    document.querySelectorAll(".tab-btn").forEach(btn => {
        btn.addEventListener("click", () => {
            const tab = btn.dataset.tab;
            switchTab(tab);
        });
    });
}

function switchTab(tab) {
    const user = Storage.getUser();
    if (user?.rol === "admin") {
        if (!tab.startsWith("admin-")) tab = "admin-dashboard";
    } else if (user?.rol === "operador") {
        if (tab !== "requests" && tab !== "slices") tab = "requests";
    } else {
        if (tab.startsWith("admin-")) tab = "drafts";
    }

    activeTab = tab;
    document.querySelectorAll(".tab-btn").forEach(btn => {
        const isActive = btn.dataset.tab === tab;
        btn.classList.toggle("text-blue-900", isActive);
        btn.classList.toggle("border-blue-900", isActive);
        btn.classList.toggle("text-slate-500", !isActive);
        btn.classList.toggle("border-transparent", !isActive);
    });

    // Ocultar/mostrar paneles estándar
    const paneDrafts = document.getElementById("tab-pane-drafts");
    const paneRequests = document.getElementById("tab-pane-requests");
    const paneSlices = document.getElementById("tab-pane-slices");
    const paneInfra = document.getElementById("tab-pane-infra");
    if (paneDrafts) paneDrafts.classList.toggle("hidden", tab !== "drafts");
    if (paneRequests) paneRequests.classList.toggle("hidden", tab !== "requests");
    if (paneSlices) paneSlices.classList.toggle("hidden", tab !== "slices");
    if (paneInfra) paneInfra.classList.toggle("hidden", tab !== "infra");

    // Ocultar/mostrar paneles exclusivos de Admin
    const paneAdminDash = document.getElementById("tab-pane-admin-dashboard");
    const paneAdminInfra = document.getElementById("tab-pane-admin-infra");
    const paneAdminUsers = document.getElementById("tab-pane-admin-users");
    const paneAdminQuotas = document.getElementById("tab-pane-admin-quotas");
    const paneAdminAudit = document.getElementById("tab-pane-admin-audit");
    if (paneAdminDash) paneAdminDash.classList.toggle("hidden", tab !== "admin-dashboard");
    if (paneAdminInfra) paneAdminInfra.classList.toggle("hidden", tab !== "admin-infra");
    if (paneAdminUsers) paneAdminUsers.classList.toggle("hidden", tab !== "admin-users");
    if (paneAdminQuotas) paneAdminQuotas.classList.toggle("hidden", tab !== "admin-quotas");
    if (paneAdminAudit) paneAdminAudit.classList.toggle("hidden", tab !== "admin-audit");

    if (tab === "drafts") Drafts.load("drafts-table-container");
    if (tab === "requests") Requests.load("requests-table-container");
    if (tab === "slices") Slices.load("slices-table-container");
    if (tab === "infra") loadInfraData("infra-container");

    // Paneles de Administrador
    if (tab === "admin-dashboard") AdminView.loadDashboard("admin-dashboard-container");
    if (tab === "admin-infra") AdminView.loadCatalogs("admin-infra-container");
    if (tab === "admin-users") AdminView.loadUsers("admin-users-container");
    if (tab === "admin-quotas") AdminView.loadQuotas("admin-quotas-container");
    if (tab === "admin-audit") AdminView.loadAudit("admin-audit-container");
}

function initGlobalButtons() {
    // Botón Actualizar (Solo lectura GET)
    const btnRefresh = document.getElementById("btn-refresh-all");
    if (btnRefresh) {
        btnRefresh.addEventListener("click", async () => {
            btnRefresh.classList.add("opacity-50");
            await refreshAllData();
            btnRefresh.classList.remove("opacity-50");
        });
    }

    // Botón Crear Borrador
    const btnCreateDraft = document.getElementById("btn-new-draft");
    if (btnCreateDraft) {
        btnCreateDraft.addEventListener("click", () => {
            Wizard.open(null, () => Drafts.load("drafts-table-container"));
        });
    }

    // Botón Importar JSON
    const fileInput = document.getElementById("file-import-json");
    const btnImport = document.getElementById("btn-import-draft");
    if (btnImport && fileInput) {
        btnImport.addEventListener("click", () => fileInput.click());
        fileInput.addEventListener("change", (e) => {
            const file = e.target.files[0];
            if (file) {
                Drafts.importJsonFile(file, () => Drafts.load("drafts-table-container"));
                fileInput.value = "";
            }
        });
    }

    // Cerrar modales con 'Escape' o clic en fondo
    const closeWizard = document.getElementById("wizard-close-btn");
    if (closeWizard) closeWizard.onclick = () => Wizard.close();

    const closeTopo = document.getElementById("topo-close-btn");
    if (closeTopo) closeTopo.onclick = () => Slices.closeTopologyModal();

}

async function refreshAllData() {
    const user = Storage.getUser();
    if (user?.rol === "admin") {
        if (activeTab === "admin-dashboard") await AdminView.loadDashboard("admin-dashboard-container");
        else if (activeTab === "admin-infra") await AdminView.loadCatalogs("admin-infra-container");
        else if (activeTab === "admin-users") await AdminView.loadUsers("admin-users-container");
        else if (activeTab === "admin-quotas") await AdminView.loadQuotas("admin-quotas-container");
        return;
    }

    if (user?.rol === "consumidor") {
        await Quotas.load("quotas-container");
    } else if (user?.rol === "operador") {
        await updateOperatorCounters();
    }
    if (activeTab === "drafts") await Drafts.load("drafts-table-container");
    if (activeTab === "requests") await Requests.load("requests-table-container");
    if (activeTab === "slices") await Slices.load("slices-table-container");
    if (activeTab === "infra") await loadInfraData("infra-container");
}

async function updateOperatorCounters() {
    try {
        const [reqs, slices] = await Promise.all([
            API.query.getRequests().catch(() => []),
            API.query.getSlices().catch(() => [])
        ]);
        const pendingCount = (reqs || []).filter(r => r.estado === "PENDING").length;
        const runningCount = (slices || []).filter(s => s.estado === "RUNNING" && s.version_activa_id !== null).length;

        const elPending = document.getElementById("op-pending-count");
        const elPendingBadge = document.getElementById("op-pending-badge");
        const elRunning = document.getElementById("op-running-count");

        if (elPending) elPending.innerText = pendingCount;
        if (elPendingBadge) elPendingBadge.innerText = `${pendingCount} PENDIENTE${pendingCount !== 1 ? 'S' : ''}`;
        if (elRunning) elRunning.innerText = runningCount;
    } catch (e) {
        // Ignorar
    }
}

function startSmartPolling() {
    stopSmartPolling();
    autoPollingInterval = setInterval(async () => {
        const user = Storage.getUser();
        if (user?.rol === "consumidor") {
            await Quotas.load("quotas-container");
        } else if (user?.rol === "operador") {
            await updateOperatorCounters();
        }
        if (activeTab === "requests") await Requests.load("requests-table-container");
        if (activeTab === "slices") await Slices.load("slices-table-container");
    }, 5000);
}

function stopSmartPolling() {
    if (autoPollingInterval) {
        clearInterval(autoPollingInterval);
        autoPollingInterval = null;
    }
}


async function loadInfraData(containerId) {
    const container = document.getElementById(containerId);
    if (!container) return;

    container.innerHTML = `
        <div class="p-8 text-center text-slate-400">
            <span class="inline-block animate-spin mr-2">⟳</span> Consultando catálogos e infraestructura física...
        </div>
    `;

    try {
        const [zones, flavors, images] = await Promise.all([
            API.query.getZones().catch(() => []),
            API.query.getFlavors(true).catch(() => []),
            API.query.getImages(true).catch(() => [])
        ]);

        const flavorRows = (flavors || []).map(f => `
            <tr class="border-b border-slate-100 text-xs">
                <td class="p-2.5 font-bold font-mono text-slate-800">${f.name}</td>
                <td class="p-2.5 text-slate-600">${f.vcpu} Core${f.vcpu > 1 ? 's' : ''}</td>
                <td class="p-2.5 text-slate-600">${f.vram_mb} MB</td>
                <td class="p-2.5 text-slate-600">${f.vdisk_mb} MB</td>
                <td class="p-2.5">
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${f.activo ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}">
                        ${f.activo ? 'ACTIVO' : 'INACTIVO'}
                    </span>
                </td>
            </tr>
        `).join("");

        const imageRows = (images || []).map(img => `
            <tr class="border-b border-slate-100 text-xs">
                <td class="p-2.5 font-bold text-slate-800">${img.nombre}</td>
                <td class="p-2.5 font-mono text-slate-600">${img.formato || 'QCOW2/RAW'}</td>
                <td class="p-2.5">
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${img.estado === 'ACTIVE' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}">
                        ${img.estado === 'ACTIVE' ? 'ACTIVO' : 'INACTIVO'}
                    </span>
                </td>
            </tr>
        `).join("");

        const zonesRows = (zones || []).map(z => `
            <tr class="border-b border-slate-100 text-xs">
                <td class="p-2.5 font-bold text-slate-800">${z.nombre}</td>
                <td class="p-2.5 text-slate-600">${z.cluster_nombre || 'Linux KVM'}</td>
                <td class="p-2.5">
                    <span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-100 text-emerald-800">
                        ${z.estado || 'ACTIVE'}
                    </span>
                </td>
            </tr>
        `).join("");

        container.innerHTML = `
            <div class="grid grid-cols-1 md:grid-cols-3 gap-6">
                <!-- Sabores (Flavors) -->
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                    <div class="p-3 bg-slate-50 border-b border-slate-200">
                        <h5 class="text-xs font-bold uppercase tracking-wider text-slate-700">Sabores (Flavors)</h5>
                    </div>
                    <table class="w-full text-left">
                        <thead>
                            <tr class="bg-slate-50/50 border-b border-slate-200 text-slate-500 text-[10px] uppercase font-bold">
                                <th class="p-2.5">Nombre</th>
                                <th class="p-2.5">vCPU</th>
                                <th class="p-2.5">RAM</th>
                                <th class="p-2.5">Disco</th>
                                <th class="p-2.5">Estado</th>
                            </tr>
                        </thead>
                        <tbody>${flavorRows}</tbody>
                    </table>
                </div>

                <!-- Imágenes -->
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                    <div class="p-3 bg-slate-50 border-b border-slate-200">
                        <h5 class="text-xs font-bold uppercase tracking-wider text-slate-700">Imágenes SO</h5>
                    </div>
                    <table class="w-full text-left">
                        <thead>
                            <tr class="bg-slate-50/50 border-b border-slate-200 text-slate-500 text-[10px] uppercase font-bold">
                                <th class="p-2.5">Nombre</th>
                                <th class="p-2.5">Formato</th>
                                <th class="p-2.5">Estado</th>
                            </tr>
                        </thead>
                        <tbody>${imageRows}</tbody>
                    </table>
                </div>

                <!-- Zonas -->
                <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                    <div class="p-3 bg-slate-50 border-b border-slate-200">
                        <h5 class="text-xs font-bold uppercase tracking-wider text-slate-700">Zonas de Disponibilidad</h5>
                    </div>
                    <table class="w-full text-left">
                        <thead>
                            <tr class="bg-slate-50/50 border-b border-slate-200 text-slate-500 text-[10px] uppercase font-bold">
                                <th class="p-2.5">Zona</th>
                                <th class="p-2.5">Clúster</th>
                                <th class="p-2.5">Estado</th>
                            </tr>
                        </thead>
                        <tbody>${zonesRows}</tbody>
                    </table>
                </div>
            </div>
        `;

        if (window.lucide) window.lucide.createIcons();

    } catch (e) {
        container.innerHTML = `
            <div class="p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                Error al cargar catálogo de infraestructura: ${e.message}
            </div>
        `;
    }
}