/**
 * admin.js — Panel de Control y Módulo del Administrador de Infraestructura
 * Orquestador Cloud TEL141 - Grupo 3
 * 
 * Permisos:
 * - Supervisión global de infraestructura, clúster y telemetría en vivo (Grafana / Prometheus).
 * - Gestión de identidad: Cuentas de usuario, roles y niveles de servicio.
 * - Políticas de plataforma: Definición y actualización de cuotas para nivel básico y avanzado.
 * - Catálogos de infraestructura: Zonas de disponibilidad, flavors e imágenes.
 * - Intervención administrativa justificada ante incidentes, fallos o abandono de slices.
 * 
 * Denegaciones estrictas:
 * - NO participa en aprobación o rechazo rutinario de solicitudes individuales.
 * - Toda intervención en slices debe tener un motivo administrativo documentado y registrado.
 */

import { API, Storage } from "./api.js";

let adminUsersList = [];
let adminQuotasList = [];
let adminSlicesList = [];
let adminServersList = [];
let adminZonesList = [];
let adminFlavorsList = [];
let adminImagesList = [];
let adminAuditLogsList = [];
let adminAnomaliesList = [];

export const AdminView = {
    // =========================================================================
    // 1. DASHBOARD & MONITOREO (Vista Principal Inicial)
    // =========================================================================
    loadDashboard: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="p-8 text-center text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Cargando Dashboard Global y Métricas del Clúster...
            </div>
        `;

        try {
            const [metrics, servers, slices] = await Promise.all([
                API.query.getMetrics().catch(() => null),
                API.query.getServers().catch(() => []),
                API.admin.getAllSlices().catch(() => [])
            ]);

            adminServersList = servers || [];
            adminSlicesList = slices || [];
            const deployedSlices = adminSlicesList.filter(s => s.estado !== "DRAFT" && s.estado !== "ELIMINATED");

            // 1.1 Tarjeta de Métricas Globales
            const metricsHtml = `
                <div class="bg-gradient-to-r from-blue-950 via-slate-900 to-indigo-950 text-white p-6 rounded-2xl shadow-sm mb-6 border border-slate-800">
                    <div class="flex items-center justify-between mb-4">
                        <div>
                            <span class="text-[10px] font-bold uppercase tracking-widest text-blue-300">Infraestructura y Plataforma Cloud</span>
                            <h3 class="text-base font-bold text-white">Métricas de Capacidad Global del Clúster</h3>
                        </div>
                        <span class="px-3 py-1 rounded-full text-xs font-bold bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 flex items-center">
                            <span class="w-2 h-2 rounded-full bg-emerald-400 mr-2 animate-pulse"></span>
                            CLÚSTER OPERATIVO
                        </span>
                    </div>

                    <div class="grid grid-cols-2 md:grid-cols-4 gap-4 text-center">
                        <div class="bg-white/5 p-4 rounded-xl border border-white/10">
                            <span class="block text-3xl font-extrabold text-white">${deployedSlices.length}</span>
                            <span class="text-xs text-blue-200 uppercase font-semibold">Slices Desplegados</span>
                        </div>
                        <div class="bg-white/5 p-4 rounded-xl border border-white/10">
                            <span class="block text-3xl font-extrabold text-emerald-400">${deployedSlices.filter(s => s.estado === 'RUNNING').length}</span>
                            <span class="text-xs text-blue-200 uppercase font-semibold">Slices en Ejecución</span>
                        </div>
                        <div class="bg-white/5 p-4 rounded-xl border border-white/10">
                            <span class="block text-3xl font-extrabold text-white">${metrics?.active_servers ?? adminServersList.length}</span>
                            <span class="text-xs text-blue-200 uppercase font-semibold">Hipervisores KVM Activos</span>
                        </div>
                        <div class="bg-white/5 p-4 rounded-xl border border-white/10">
                            <span class="block text-3xl font-extrabold text-indigo-300">${metrics?.total_vcpu_allocated ?? 0}</span>
                            <span class="text-xs text-blue-200 uppercase font-semibold">vCPUs Asignadas</span>
                        </div>
                    </div>
                </div>
            `;

            // 1.2 Módulo de Monitoreo en Vivo (Grafana & Prometheus)
            const monitoringHtml = `
                <div class="space-y-3 mb-6">
                    <div class="flex items-center justify-between">
                        <div>
                            <h4 class="text-xs font-bold uppercase tracking-wider text-slate-800 flex items-center">
                                <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 mr-2 animate-pulse"></span>
                                Monitoreo y Métricas en Vivo de Orquestación (Temporal.io)
                            </h4>
                            <p class="text-[11px] text-slate-500">Métricas scrapeadas por Prometheus y visualizadas en tiempo real desde Grafana</p>
                        </div>
                        <div class="flex items-center space-x-2">
                            <a href="http://localhost:3001/d/temporal-overview?orgId=1" target="_blank" class="bg-blue-900 hover:bg-blue-950 text-white px-3 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition flex items-center space-x-1 shadow-sm">
                                <span>ABRIR EN GRAFANA (3001)</span>
                                <span>↗</span>
                            </a>
                            <a href="http://localhost:9090/targets" target="_blank" class="bg-slate-100 hover:bg-slate-200 text-slate-700 px-3 py-1.5 rounded-lg text-xs font-semibold uppercase tracking-wider transition">
                                <span>PROMETHEUS (9090)</span>
                            </a>
                        </div>
                    </div>
                    
                    <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden" style="height: 540px;">
                        <iframe 
                            src="http://localhost:3001/d/temporal-overview?kiosk&theme=light" 
                            class="w-full h-full border-0" 
                            title="Dashboard de Monitoreo Temporal.io"
                        ></iframe>
                    </div>
                </div>
            `;

            // 1.3 Hipervisores Físicos
            const serversHtml = `
                <div class="mb-6">
                    <div class="flex items-center justify-between mb-3">
                        <h4 class="text-xs font-bold uppercase tracking-wider text-slate-700">
                            Hipervisores Físicos Disponibles (Nodos de Cómputo)
                        </h4>
                        <span class="text-xs text-slate-500">${adminServersList.length} Servidores Registrados</span>
                    </div>
                    <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
                        ${adminServersList.map(s => `
                            <div class="p-4 bg-white rounded-xl border border-slate-200 shadow-sm space-y-2">
                                <div class="flex items-center justify-between">
                                    <span class="font-bold text-slate-900 text-sm">Servidor #${s.id}</span>
                                    <span class="px-2 py-0.5 rounded-full text-xs font-bold ${s.estado === 'ACTIVE' ? 'bg-emerald-100 text-emerald-800 border border-emerald-300' : 'bg-slate-100 text-slate-700'}">
                                        ${s.estado}
                                    </span>
                                </div>
                                <div class="font-mono text-xs text-slate-600 bg-slate-50 p-2 rounded border border-slate-100 space-y-1">
                                    <div><b>IP Servidor:</b> ${s.ip_serv}</div>
                                    <div><b>MAC:</b> ${s.mac_serv || 'N/A'}</div>
                                    <div><b>Zona:</b> ${s.zona_nombre || `Zona ${s.zona_id || 1}`}</div>
                                </div>
                                <div class="grid grid-cols-3 gap-2 text-center text-xs pt-2">
                                    <div class="bg-slate-50 p-2 rounded">
                                        <span class="block font-bold text-slate-800">${s.total_cpu_cores}</span>
                                        <span class="text-[10px] text-slate-500 uppercase">Cores CPU</span>
                                    </div>
                                    <div class="bg-slate-50 p-2 rounded">
                                        <span class="block font-bold text-slate-800">${(s.total_ram_mb / 1024).toFixed(1)} GB</span>
                                        <span class="text-[10px] text-slate-500 uppercase">RAM Total</span>
                                    </div>
                                    <div class="bg-slate-50 p-2 rounded">
                                        <span class="block font-bold text-slate-800">${(s.total_disk_mb / 1024).toFixed(1)} GB</span>
                                        <span class="text-[10px] text-slate-500 uppercase">Disco</span>
                                    </div>
                                </div>
                            </div>
                        `).join("")}
                    </div>
                </div>
            `;

            // 1.4 Supervisión Global de Slices Desplegados (Excluye Borradores / DRAFT)
            const slicesRows = deployedSlices.map(sl => {
                const isRunning = sl.estado === "RUNNING";
                const isUpdating = sl.estado === "UPDATING";
                const isStopped = sl.estado === "STOPPED";
                const isFailed = sl.estado === "FAILED";

                let badge = "";
                if (isRunning) badge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 border border-emerald-300">ACTIVO / RUNNING</span>`;
                else if (isUpdating) badge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-sky-100 text-sky-800 border border-sky-300">EN DESPLIEGUE</span>`;
                else if (isStopped) badge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-800 border border-amber-300">DETENIDO</span>`;
                else if (isFailed) badge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-800 border border-red-300">FALLIDO</span>`;
                else badge = `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-slate-100 text-slate-700">${sl.estado}</span>`;

                return `
                    <tr class="border-b border-slate-100 hover:bg-slate-50 text-xs">
                        <td class="p-3 font-mono font-bold text-slate-900">#${sl.id}</td>
                        <td class="p-3 font-semibold text-slate-800">${sl.nombre}</td>
                        <td class="p-3 font-mono text-blue-900">Usuario #${sl.usuario_id}</td>
                        <td class="p-3 text-slate-600">${sl.zona_nombre || `Zona ${sl.zona_id || 1}`}</td>
                        <td class="p-3">${badge}</td>
                        <td class="p-3 text-slate-500">${sl.fecha_creacion ? new Date(sl.fecha_creacion).toLocaleString() : '-'}</td>
                        <td class="p-3 text-right">
                            <button data-sliceid="${sl.id}" data-slicename="${sl.nombre}" class="btn-admin-intervene bg-amber-700 hover:bg-amber-800 text-white px-2.5 py-1 rounded text-xs font-semibold uppercase tracking-wider transition shadow-sm">
                                INTERVENIR SLICE
                            </button>
                        </td>
                    </tr>
                `;
            }).join("");

            const slicesHtml = `
                <div class="space-y-3">
                    <div class="flex items-center justify-between">
                        <div>
                            <h4 class="text-xs font-bold uppercase tracking-wider text-slate-700">
                                Supervisión Global de Slices Desplegados en la Plataforma
                            </h4>
                            <p class="text-[11px] text-slate-500">
                                Permiso administrativo: Detener o eliminar slices ante incidentes, fallos o abandono, dejando registro auditable (excluye borradores).
                            </p>
                        </div>
                        <span class="text-xs text-slate-500">${deployedSlices.length} Slices Desplegados</span>
                    </div>

                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="bg-slate-50 border-b border-slate-200 text-slate-600 text-[11px] font-bold uppercase tracking-wider">
                                    <th class="p-3">ID</th>
                                    <th class="p-3">Nombre del Slice</th>
                                    <th class="p-3">Propietario</th>
                                    <th class="p-3">Zona</th>
                                    <th class="p-3">Estado</th>
                                    <th class="p-3">Fecha Creación</th>
                                    <th class="p-3 text-right">Acción Administrativa</th>
                                </tr>
                            </thead>
                            <tbody>
                                ${slicesRows || `<tr><td colspan="7" class="p-8 text-center text-slate-400 text-xs">No hay slices desplegados actualmente en la infraestructura física.</td></tr>`}
                            </tbody>
                        </table>
                    </div>
                </div>
            `;

            container.innerHTML = `
                ${metricsHtml}
                ${monitoringHtml}
                ${serversHtml}
                ${slicesHtml}
            `;

            // Eventos de botones de intervención
            container.querySelectorAll(".btn-admin-intervene").forEach(btn => {
                btn.addEventListener("click", () => {
                    const sliceId = btn.dataset.sliceid;
                    const sliceName = btn.dataset.slicename;
                    AdminView.openInterveneModal(sliceId, sliceName);
                });
            });

        } catch (e) {
            container.innerHTML = `
                <div class="p-6 bg-red-50 border border-red-200 text-red-700 rounded-xl text-sm">
                    Error al cargar Dashboard de Administrador: ${e.message}
                </div>
            `;
        }
    },

    // =========================================================================
    // 2. INFRAESTRUCTURA Y CATÁLOGOS
    // =========================================================================
    loadCatalogs: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="p-8 text-center text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Consultando catálogos de infraestructura...
            </div>
        `;

        try {
            const [zones, flavors, images] = await Promise.all([
                API.query.getZones().catch(() => []),
                API.query.getFlavors(false).catch(() => []),
                API.query.getImages(false).catch(() => [])
            ]);

            adminZonesList = zones || [];
            adminFlavorsList = flavors || [];
            adminImagesList = images || [];

            const zonesRows = adminZonesList.map(z => `
                <tr class="border-b border-slate-100 text-xs">
                    <td class="p-3 font-mono font-bold text-slate-900">#${z.id}</td>
                    <td class="p-3 font-bold text-slate-800">${z.nombre}</td>
                    <td class="p-3 text-slate-600">${z.cluster_nombre || 'Linux KVM'}</td>
                    <td class="p-3">
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${z.estado === 'ACTIVE' ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'}">
                            ${z.estado || 'ACTIVE'}
                        </span>
                    </td>
                    <td class="p-3 text-right">
                        <button data-zoneid="${z.id}" class="btn-toggle-zone bg-slate-100 hover:bg-slate-200 text-slate-700 px-2 py-1 rounded text-xs font-semibold">
                            ${z.estado === 'ACTIVE' ? 'DESHABILITAR' : 'HABILITAR'}
                        </button>
                    </td>
                </tr>
            `).join("");

            const flavorRows = adminFlavorsList.map(f => `
                <tr class="border-b border-slate-100 text-xs">
                    <td class="p-3 font-mono font-bold text-slate-900">${f.name}</td>
                    <td class="p-3 text-slate-700">${f.vcpu} Core${f.vcpu > 1 ? 's' : ''}</td>
                    <td class="p-3 text-slate-700">${f.vram_mb} MB</td>
                    <td class="p-3 text-slate-700">${f.vdisk_mb} MB</td>
                    <td class="p-3">
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${f.activo ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}">
                            ${f.activo ? 'ACTIVO' : 'INACTIVO'}
                        </span>
                    </td>
                    <td class="p-3 text-right">
                        <button data-flavorid="${f.id}" class="btn-toggle-flavor bg-slate-100 hover:bg-slate-200 text-slate-700 px-2 py-1 rounded text-xs font-semibold">
                            ${f.activo ? 'DESACTIVAR' : 'ACTIVAR'}
                        </button>
                    </td>
                </tr>
            `).join("");

            const imageRows = adminImagesList.map(img => `
                <tr class="border-b border-slate-100 text-xs">
                    <td class="p-3 font-bold text-slate-900">${img.nombre}</td>
                    <td class="p-3 font-mono text-slate-600">${img.cluster_compatible || 'linux'}</td>
                    <td class="p-3 font-mono text-slate-600">${img.ruta_referencia || '/var/lib/images'}</td>
                    <td class="p-3">
                        <span class="px-2 py-0.5 rounded-full text-[10px] font-bold ${img.estado === 'ACTIVE' ? 'bg-emerald-100 text-emerald-800' : 'bg-slate-100 text-slate-600'}">
                            ${img.estado || 'ACTIVE'}
                        </span>
                    </td>
                    <td class="p-3 text-slate-500 text-xs">
                        ${img.contador_refs ? `${img.contador_refs} instancias activas` : 'Sin dependencias'}
                    </td>
                </tr>
            `).join("");

            container.innerHTML = `
                <div class="space-y-6">
                    <div>
                        <h3 class="text-sm font-bold text-slate-900">Catálogos de Infraestructura Física</h3>
                        <p class="text-xs text-slate-500">Administra zonas de disponibilidad, sabores de VMs y catálogo de imágenes base.</p>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <!-- Zonas -->
                        <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                            <div class="p-3 bg-slate-50 border-b border-slate-200 flex justify-between items-center">
                                <h5 class="text-xs font-bold uppercase tracking-wider text-slate-700">Zonas de Disponibilidad</h5>
                                <span class="text-[11px] text-slate-400">Total: ${adminZonesList.length}</span>
                            </div>
                            <table class="w-full text-left border-collapse">
                                <thead>
                                    <tr class="bg-slate-50/50 border-b border-slate-200 text-slate-500 text-[10px] uppercase font-bold">
                                        <th class="p-3">ID</th>
                                        <th class="p-3">Nombre</th>
                                        <th class="p-3">Clúster</th>
                                        <th class="p-3">Estado</th>
                                        <th class="p-3 text-right">Acción</th>
                                    </tr>
                                </thead>
                                <tbody>${zonesRows}</tbody>
                            </table>
                        </div>

                        <!-- Sabores -->
                        <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                            <div class="p-3 bg-slate-50 border-b border-slate-200 flex justify-between items-center">
                                <h5 class="text-xs font-bold uppercase tracking-wider text-slate-700">Catálogo de Sabores (Flavors)</h5>
                                <span class="text-[11px] text-slate-400">Total: ${adminFlavorsList.length}</span>
                            </div>
                            <table class="w-full text-left border-collapse">
                                <thead>
                                    <tr class="bg-slate-50/50 border-b border-slate-200 text-slate-500 text-[10px] uppercase font-bold">
                                        <th class="p-3">Nombre</th>
                                        <th class="p-3">vCPU</th>
                                        <th class="p-3">RAM</th>
                                        <th class="p-3">Disco</th>
                                        <th class="p-3">Estado</th>
                                        <th class="p-3 text-right">Acción</th>
                                    </tr>
                                </thead>
                                <tbody>${flavorRows}</tbody>
                            </table>
                        </div>
                    </div>

                    <!-- Imágenes -->
                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <div class="p-3 bg-slate-50 border-b border-slate-200 flex justify-between items-center">
                            <div>
                                <h5 class="text-xs font-bold uppercase tracking-wider text-slate-700">Imágenes del Sistema Operativo</h5>
                                <p class="text-[11px] text-slate-400">Protección de integridad: No se permite eliminar imágenes con dependencias activas.</p>
                            </div>
                            <span class="text-[11px] text-slate-400">Total: ${adminImagesList.length}</span>
                        </div>
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="bg-slate-50/50 border-b border-slate-200 text-slate-500 text-[10px] uppercase font-bold">
                                    <th class="p-3">Nombre Imagen</th>
                                    <th class="p-3">Clúster Compatible</th>
                                    <th class="p-3">Ruta Interna</th>
                                    <th class="p-3">Estado</th>
                                    <th class="p-3">Dependencias</th>
                                </tr>
                            </thead>
                            <tbody>${imageRows}</tbody>
                        </table>
                    </div>
                </div>
            `;

            // Handlers para Zonas y Flavors
            container.querySelectorAll(".btn-toggle-zone").forEach(btn => {
                btn.addEventListener("click", async () => {
                    const zoneId = btn.dataset.zoneid;
                    try {
                        await API.admin.toggleZone(zoneId);
                        AdminView.loadCatalogs(containerId);
                    } catch (e) {
                        alert(`Error al cambiar estado de zona: ${e.message}`);
                    }
                });
            });

            container.querySelectorAll(".btn-toggle-flavor").forEach(btn => {
                btn.addEventListener("click", async () => {
                    const flavorId = btn.dataset.flavorid;
                    try {
                        await API.admin.toggleFlavor(flavorId);
                        AdminView.loadCatalogs(containerId);
                    } catch (e) {
                        alert(`Error al cambiar estado de flavor: ${e.message}`);
                    }
                });
            });

        } catch (e) {
            container.innerHTML = `
                <div class="p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                    Error al cargar catálogos: ${e.message}
                </div>
            `;
        }
    },

    // =========================================================================
    // 3. GESTIÓN DE IDENTIDAD Y USUARIOS
    // =========================================================================
    loadUsers: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="p-8 text-center text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Consultando directorio de usuarios...
            </div>
        `;

        try {
            adminUsersList = await API.admin.getUsers();

            const userRows = adminUsersList.map(u => {
                let rolBadge = "";
                if (u.rol === "admin") rolBadge = `<span class="px-2 py-0.5 rounded-full text-xs font-bold bg-slate-900 text-white">ADMINISTRADOR</span>`;
                else if (u.rol === "operador") rolBadge = `<span class="px-2 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-900 border border-amber-300">OPERADOR</span>`;
                else rolBadge = `<span class="px-2 py-0.5 rounded-full text-xs font-bold bg-blue-100 text-blue-800 border border-blue-200">CONSUMIDOR</span>`;

                let nivelBadge = "-";
                if (u.nivel === "avanzado") nivelBadge = `<span class="px-2 py-0.5 rounded-full text-xs font-bold bg-purple-100 text-purple-800">AVANZADO</span>`;
                else if (u.nivel === "basico") nivelBadge = `<span class="px-2 py-0.5 rounded-full text-xs font-bold bg-blue-50 text-blue-700">BÁSICO</span>`;

                const isAct = u.estado === "ACTIVE";

                return `
                    <tr class="border-b border-slate-100 hover:bg-slate-50 text-xs">
                        <td class="p-3 font-mono font-bold text-slate-900">#${u.id}</td>
                        <td class="p-3 font-mono font-bold text-blue-950">${u.codigo}</td>
                        <td class="p-3">${rolBadge}</td>
                        <td class="p-3">${nivelBadge}</td>
                        <td class="p-3">
                            <span class="px-2 py-0.5 rounded-full text-xs font-bold ${isAct ? 'bg-emerald-100 text-emerald-800' : 'bg-red-100 text-red-800'}">
                                ${u.estado}
                            </span>
                        </td>
                        <td class="p-3 text-slate-500">${u.fecha_creacion ? new Date(u.fecha_creacion).toLocaleDateString() : '-'}</td>
                        <td class="p-3 text-right">
                            <button onclick="AdminView.openEditUserById(${u.id})" data-userid="${u.id}" data-usercodigo="${u.codigo}" data-userrol="${u.rol}" data-usernivel="${u.nivel || ''}" data-userestado="${u.estado}" class="btn-admin-edit-user bg-slate-100 hover:bg-slate-200 text-slate-700 px-2.5 py-1 rounded text-xs font-semibold">
                                MODIFICAR
                            </button>
                        </td>
                    </tr>
                `;
            }).join("");

            container.innerHTML = `
                <div class="space-y-4">
                    <div class="flex items-center justify-between">
                        <div>
                            <h3 class="text-sm font-bold text-slate-900">Gestión de Cuentas, Roles y Niveles de Identidad</h3>
                            <p class="text-xs text-slate-500">Crea nuevos usuarios, asigna roles de plataforma y define niveles de servicio.</p>
                        </div>
                        <button type="button" id="btn-admin-new-user" onclick="AdminView.openCreateUserModal()" class="bg-blue-900 hover:bg-blue-950 text-white px-3.5 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition shadow-sm flex items-center space-x-1">
                            <span>+ REGISTRAR NUEVO USUARIO</span>
                        </button>
                    </div>

                    <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                        <table class="w-full text-left border-collapse">
                            <thead>
                                <tr class="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs font-semibold uppercase tracking-wider">
                                    <th class="p-3">ID</th>
                                    <th class="p-3">Código de Usuario</th>
                                    <th class="p-3">Rol</th>
                                    <th class="p-3">Nivel de Servicio</th>
                                    <th class="p-3">Estado</th>
                                    <th class="p-3">Fecha de Registro</th>
                                    <th class="p-3 text-right">Acciones</th>
                                </tr>
                            </thead>
                            <tbody>${userRows}</tbody>
                        </table>
                    </div>
                </div>
            `;

            // Botón nuevo usuario
            const btnNewUser = document.getElementById("btn-admin-new-user");
            if (btnNewUser) {
                btnNewUser.onclick = () => AdminView.openCreateUserModal();
            }

            // Botones modificar usuario
            container.querySelectorAll(".btn-admin-edit-user").forEach(btn => {
                btn.onclick = () => {
                    const u = {
                        id: btn.dataset.userid,
                        codigo: btn.dataset.usercodigo,
                        rol: btn.dataset.userrol,
                        nivel: btn.dataset.usernivel,
                        estado: btn.dataset.userestado
                    };
                    AdminView.openEditUserModal(u);
                };
            });

        } catch (e) {
            container.innerHTML = `
                <div class="p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                    Error al cargar usuarios: ${e.message}
                </div>
            `;
        }
    },

    // =========================================================================
    // 4. POLÍTICAS Y CUOTAS DE RECURSOS
    // =========================================================================
    loadQuotas: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="p-8 text-center text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Consultando políticas de cuotas por nivel...
            </div>
        `;

        try {
            adminQuotasList = await API.admin.getQuotas();

            const basicQuotas = adminQuotasList.filter(q => q.nivel_id === 1);
            const advQuotas = adminQuotasList.filter(q => q.nivel_id === 2);

            const getVal = (list, rec) => {
                const found = list.find(q => q.recurso === rec);
                return found ? found.prometido : 0;
            };

            container.innerHTML = `
                <div class="space-y-6">
                    <div>
                        <h3 class="text-sm font-bold text-slate-900">Políticas y Cuotas de Recursos por Nivel de Servicio</h3>
                        <p class="text-xs text-slate-500">Configura los límites máximos permitidos de vCPU, memoria RAM y almacenamiento por nivel.</p>
                    </div>

                    <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                        <!-- Tarjeta Nivel Básico -->
                        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4">
                            <div class="flex items-center justify-between pb-3 border-b border-slate-100">
                                <div>
                                    <span class="text-[10px] font-bold uppercase tracking-wider text-blue-600">Nivel Estándar</span>
                                    <h4 class="text-base font-bold text-slate-900">NIVEL BÁSICO</h4>
                                </div>
                                <span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-blue-100 text-blue-800">
                                    Requiere Evaluación Operador
                                </span>
                            </div>

                            <div class="space-y-3">
                                <div class="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                                    <span class="text-xs font-semibold text-slate-700">vCPUs Máximos:</span>
                                    <span class="font-mono font-bold text-slate-900">${getVal(basicQuotas, 'vcpu')} Cores</span>
                                </div>
                                <div class="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                                    <span class="text-xs font-semibold text-slate-700">Memoria RAM Máxima:</span>
                                    <span class="font-mono font-bold text-slate-900">${(getVal(basicQuotas, 'ram_mb') / 1024).toFixed(1)} GB (${getVal(basicQuotas, 'ram_mb')} MB)</span>
                                </div>
                                <div class="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                                    <span class="text-xs font-semibold text-slate-700">Almacenamiento Máximo:</span>
                                    <span class="font-mono font-bold text-slate-900">${(getVal(basicQuotas, 'disk_mb') / 1000).toFixed(1)} GB (${getVal(basicQuotas, 'disk_mb')} MB)</span>
                                </div>
                            </div>

                            <button type="button" class="btn-edit-quota-level w-full bg-slate-100 hover:bg-slate-200 text-slate-800 py-2 rounded-lg text-xs font-bold uppercase tracking-wider transition" data-nivelid="1" data-nivelname="Básico">
                                ACTUALIZAR CUOTAS NIVEL BÁSICO
                            </button>
                        </div>

                        <!-- Tarjeta Nivel Avanzado -->
                        <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-6 space-y-4">
                            <div class="flex items-center justify-between pb-3 border-b border-slate-100">
                                <div>
                                    <span class="text-[10px] font-bold uppercase tracking-wider text-purple-600">Nivel Prioritario</span>
                                    <h4 class="text-base font-bold text-slate-900">NIVEL AVANZADO</h4>
                                </div>
                                <span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-purple-100 text-purple-800">
                                    Despliegue Directo Automático
                                </span>
                            </div>

                            <div class="space-y-3">
                                <div class="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                                    <span class="text-xs font-semibold text-slate-700">vCPUs Máximos:</span>
                                    <span class="font-mono font-bold text-slate-900">${getVal(advQuotas, 'vcpu')} Cores</span>
                                </div>
                                <div class="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                                    <span class="text-xs font-semibold text-slate-700">Memoria RAM Máxima:</span>
                                    <span class="font-mono font-bold text-slate-900">${(getVal(advQuotas, 'ram_mb') / 1024).toFixed(1)} GB (${getVal(advQuotas, 'ram_mb')} MB)</span>
                                </div>
                                <div class="flex items-center justify-between p-3 bg-slate-50 rounded-xl">
                                    <span class="text-xs font-semibold text-slate-700">Almacenamiento Máximo:</span>
                                    <span class="font-mono font-bold text-slate-900">${(getVal(advQuotas, 'disk_mb') / 1000).toFixed(1)} GB (${getVal(advQuotas, 'disk_mb')} MB)</span>
                                </div>
                            </div>

                            <button type="button" class="btn-edit-quota-level w-full bg-slate-100 hover:bg-slate-200 text-slate-800 py-2 rounded-lg text-xs font-bold uppercase tracking-wider transition" data-nivelid="2" data-nivelname="Avanzado">
                                ACTUALIZAR CUOTAS NIVEL AVANZADO
                            </button>
                        </div>
                    </div>
                </div>
            `;

            container.querySelectorAll(".btn-edit-quota-level").forEach(btn => {
                btn.onclick = () => {
                    const nivelId = parseInt(btn.dataset.nivelid, 10);
                    const nivelName = btn.dataset.nivelname;
                    AdminView.openEditQuotaModal(nivelId, nivelName);
                };
            });

        } catch (e) {
            container.innerHTML = `
                <div class="p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                    Error al cargar cuotas: ${e.message}
                </div>
            `;
        }
    },

    // =========================================================================
    // MODALES Y DIÁLOGOS ADMINISTRATIVOS
    // =========================================================================

    openInterveneModal: (sliceId, sliceName) => {
        const modal = document.getElementById("modal-admin-intervene");
        if (!modal) return;
        document.getElementById("intervene-slice-id").innerText = `#${sliceId}`;
        document.getElementById("intervene-slice-name").innerText = sliceName;
        document.getElementById("intervene-action").value = "STOP";
        document.getElementById("intervene-reason").value = "";
        modal.classList.remove("hidden");

        const btnConfirm = document.getElementById("btn-confirm-intervene");
        btnConfirm.onclick = async () => {
            const accion = document.getElementById("intervene-action").value;
            const motivo = document.getElementById("intervene-reason").value.trim();

            if (!motivo || motivo.length < 5) {
                alert("Por norma de seguridad y consistencia, debe ingresar un motivo administrativo justificado y documentado.");
                return;
            }

            btnConfirm.disabled = true;
            btnConfirm.innerText = "REGISTRANDO...";

            try {
                await API.admin.interveneSlice(sliceId, accion, motivo);
                alert(`Intervención registrada exitosamente. Slice #${sliceId} pasó a estado ${accion === 'STOP' ? 'STOPPED' : 'ELIMINATED'}.`);
                modal.classList.add("hidden");
                AdminView.loadDashboard("admin-dashboard-container");
            } catch (e) {
                alert(`Error en intervención: ${e.message}`);
            } finally {
                btnConfirm.disabled = false;
                btnConfirm.innerText = "CONFIRMAR INTERVENCIÓN";
            }
        };
    },

    openCreateUserModal: () => {
        const modal = document.getElementById("modal-admin-create-user");
        if (!modal) return;
        document.getElementById("create-user-codigo").value = "";
        document.getElementById("create-user-password").value = "";
        document.getElementById("create-user-rol").value = "consumidor";
        document.getElementById("create-user-nivel").value = "basico";
        modal.classList.remove("hidden");

        const rolSelect = document.getElementById("create-user-rol");
        const nivelDiv = document.getElementById("create-user-nivel-container");
        rolSelect.onchange = () => {
            if (rolSelect.value === "consumidor") nivelDiv.classList.remove("hidden");
            else nivelDiv.classList.add("hidden");
        };

        const form = document.getElementById("form-admin-create-user");
        form.onsubmit = async (e) => {
            e.preventDefault();
            const codigo = document.getElementById("create-user-codigo").value.trim();
            const password = document.getElementById("create-user-password").value;
            const rol = document.getElementById("create-user-rol").value;
            const nivel = rol === "consumidor" ? document.getElementById("create-user-nivel").value : null;

            try {
                await API.admin.createUser({ codigo, password, rol, nivel });
                alert(`Usuario ${codigo} registrado con éxito.`);
                modal.classList.add("hidden");
                AdminView.loadUsers("admin-users-container");
            } catch (err) {
                alert(`Error al registrar usuario: ${err.message}`);
            }
        };
    },

    openEditUserById: (userId) => {
        const u = adminUsersList.find(x => x.id == userId);
        if (u) AdminView.openEditUserModal(u);
    },

    openEditUserModal: (user) => {
        const modal = document.getElementById("modal-admin-edit-user");
        if (!modal) return;
        document.getElementById("edit-user-id").value = user.id;
        document.getElementById("edit-user-codigo-display").innerText = user.codigo;
        document.getElementById("edit-user-rol").value = user.rol;
        document.getElementById("edit-user-estado").value = user.estado;
        document.getElementById("edit-user-password").value = "";

        const nivelContainer = document.getElementById("edit-user-nivel-container");
        const nivelSelect = document.getElementById("edit-user-nivel");
        nivelSelect.value = user.nivel || "basico";
        if (user.rol === "consumidor") nivelContainer.classList.remove("hidden");
        else nivelContainer.classList.add("hidden");

        document.getElementById("edit-user-rol").onchange = (e) => {
            if (e.target.value === "consumidor") nivelContainer.classList.remove("hidden");
            else nivelContainer.classList.add("hidden");
        };

        modal.classList.remove("hidden");

        const form = document.getElementById("form-admin-edit-user");
        form.onsubmit = async (e) => {
            e.preventDefault();
            const userId = document.getElementById("edit-user-id").value;
            const rol = document.getElementById("edit-user-rol").value;
            const estado = document.getElementById("edit-user-estado").value;
            const nivel = rol === "consumidor" ? document.getElementById("edit-user-nivel").value : null;
            const password = document.getElementById("edit-user-password").value.trim();

            const payload = { rol, estado, nivel };
            if (password) payload.password = password;

            try {
                await API.admin.updateUser(userId, payload);
                alert(`Usuario ${user.codigo} actualizado correctamente.`);
                modal.classList.add("hidden");
                AdminView.loadUsers("admin-users-container");
            } catch (err) {
                alert(`Error al actualizar usuario: ${err.message}`);
            }
        };
    },

    openEditQuotaModal: (nivelId, nivelName) => {
        const modal = document.getElementById("modal-admin-edit-quota");
        if (!modal) return;
        document.getElementById("quota-modal-nivel-title").innerText = `Cuotas para Nivel ${nivelName}`;
        document.getElementById("quota-modal-nivel-id").value = nivelId;

        const quotas = adminQuotasList.filter(q => q.nivel_id === nivelId);
        const getV = (rec) => quotas.find(q => q.recurso === rec)?.prometido || 0;

        document.getElementById("quota-input-vcpu").value = getV("vcpu");
        document.getElementById("quota-input-ram").value = getV("ram_mb");
        document.getElementById("quota-input-disk").value = getV("disk_mb");

        modal.classList.remove("hidden");

        const form = document.getElementById("form-admin-edit-quota");
        form.onsubmit = async (e) => {
            e.preventDefault();
            const nId = parseInt(document.getElementById("quota-modal-nivel-id").value, 10);
            const vcpu = parseFloat(document.getElementById("quota-input-vcpu").value);
            const ram = parseFloat(document.getElementById("quota-input-ram").value);
            const disk = parseFloat(document.getElementById("quota-input-disk").value);

            try {
                await Promise.all([
                    API.admin.updateQuota(nId, "vcpu", vcpu),
                    API.admin.updateQuota(nId, "ram_mb", ram),
                    API.admin.updateQuota(nId, "disk_mb", disk)
                ]);
                alert(`Cuotas del Nivel ${nivelName} actualizadas en base de datos.`);
                modal.classList.add("hidden");
                AdminView.loadQuotas("admin-quotas-container");
            } catch (err) {
                alert(`Error al actualizar cuotas: ${err.message}`);
            }
        };
    },

    // =========================================================================
    // 5. AUDITORÍA Y SEGURIDAD (Trazabilidad a Tiempo Completo y Purga)
    // =========================================================================
    loadAudit: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="p-8 text-center text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Consultando registros de auditoría y análisis de seguridad...
            </div>
        `;

        try {
            const [logs, anomalies, slices] = await Promise.all([
                API.admin.getAuditLogs({ limit: 100 }).catch(() => []),
                API.admin.getSecurityAnomalies().catch(() => []),
                API.admin.getAllSlices().catch(() => [])
            ]);

            adminAuditLogsList = logs || [];
            adminAnomaliesList = anomalies || [];
            adminSlicesList = slices || [];

            // 5.1 Banner de Detección de Consumo Indebido / Anomalías
            let anomalyHtml = "";
            if (adminAnomaliesList.length > 0) {
                const anomalyCards = adminAnomaliesList.map(a => `
                    <div class="bg-red-50 border border-red-300 rounded-xl p-4 flex flex-col md:flex-row items-start md:items-center justify-between gap-3 shadow-sm">
                        <div class="space-y-1">
                            <div class="flex items-center space-x-2">
                                <span class="px-2 py-0.5 rounded text-[10px] font-black uppercase tracking-wider bg-red-600 text-white">ALERTA CRÍTICA</span>
                                <span class="font-bold text-red-950 text-xs">Slice #${a.slice_id} — ${a.slice_nombre}</span>
                                <span class="font-mono text-xs text-red-800">Usuario: ${a.usuario_codigo}</span>
                            </div>
                            <p class="text-xs text-red-900 font-medium">${a.detalle}</p>
                            <span class="text-[10px] text-red-600">Detectado: ${new Date(a.fecha_deteccion).toLocaleString()}</span>
                        </div>
                        <button 
                            type="button" 
                            data-sliceid="${a.slice_id}" 
                            data-slicename="${a.slice_nombre}" 
                            class="btn-trigger-purge bg-red-700 hover:bg-red-800 text-white px-3.5 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition shadow-sm whitespace-nowrap"
                        >
                            PURGAR RECURSOS
                        </button>
                    </div>
                `).join("");

                anomalyHtml = `
                    <div class="space-y-3 bg-red-50/40 p-4 rounded-2xl border border-red-200">
                        <div class="flex items-center justify-between">
                            <h4 class="text-xs font-bold uppercase tracking-wider text-red-900 flex items-center">
                                <span class="w-2.5 h-2.5 rounded-full bg-red-600 mr-2 animate-ping"></span>
                                Detección Activa de Consumo Indebido e Infracciones (${adminAnomaliesList.length})
                            </h4>
                            <span class="text-[11px] text-red-700 font-semibold">Acción requerida para liberar clúster</span>
                        </div>
                        <div class="space-y-2">
                            ${anomalyCards}
                        </div>
                    </div>
                `;
            } else {
                anomalyHtml = `
                    <div class="bg-emerald-50 border border-emerald-200 rounded-2xl p-4 flex items-center justify-between">
                        <div class="flex items-center space-x-3">
                            <div class="w-8 h-8 rounded-full bg-emerald-100 border border-emerald-300 flex items-center justify-center text-emerald-800 font-bold text-sm">
                                ✓
                            </div>
                            <div>
                                <h4 class="text-xs font-bold uppercase tracking-wider text-emerald-950">Cumplimiento de Políticas y Cuotas Óptimo</h4>
                                <p class="text-xs text-emerald-800">Todos los slices activos cumplen con los límites de vCPU, RAM y almacenamiento asignados.</p>
                            </div>
                        </div>
                        <span class="text-xs font-mono font-bold text-emerald-900 px-3 py-1 bg-emerald-100 rounded-lg">0 INFRACCIONES</span>
                    </div>
                `;
            }

            // 5.2 Contadores de Auditoría
            const loginCount = adminAuditLogsList.filter(l => l.tipo_evento === "USER_LOGIN").length;
            const purgeCount = adminAuditLogsList.filter(l => l.tipo_evento === "SECURITY_PURGE" || l.severidad === "CRITICAL").length;
            const interventionCount = adminAuditLogsList.filter(l => l.tipo_evento === "ADMIN_INTERVENTION").length;

            const countersHtml = `
                <div class="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
                        <span class="text-[10px] font-bold uppercase tracking-wider text-slate-400 block">Total Eventos Registrados</span>
                        <span class="text-2xl font-black text-slate-900">${adminAuditLogsList.length}</span>
                        <span class="text-[10px] text-slate-500 block mt-0.5">En base de datos PostgreSQL</span>
                    </div>
                    <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
                        <span class="text-[10px] font-bold uppercase tracking-wider text-blue-600 block">Inicios de Sesión</span>
                        <span class="text-2xl font-black text-blue-900">${loginCount}</span>
                        <span class="text-[10px] text-slate-500 block mt-0.5">Auditoría de identidad</span>
                    </div>
                    <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
                        <span class="text-[10px] font-bold uppercase tracking-wider text-amber-600 block">Intervenciones Admin</span>
                        <span class="text-2xl font-black text-amber-800">${interventionCount}</span>
                        <span class="text-[10px] text-slate-500 block mt-0.5">Acciones documentadas</span>
                    </div>
                    <div class="bg-white p-4 rounded-xl border border-slate-200 shadow-sm">
                        <span class="text-[10px] font-bold uppercase tracking-wider text-red-600 block">Incidentes y Purgas</span>
                        <span class="text-2xl font-black text-red-700">${purgeCount}</span>
                        <span class="text-[10px] text-slate-500 block mt-0.5">Severidad crítica / remediación</span>
                    </div>
                </div>
            `;

            // 5.3 Tabla de Logs con Filtros
            const renderLogsTable = (filteredLogs) => {
                if (!filteredLogs.length) {
                    return `<tr><td colspan="7" class="p-8 text-center text-slate-400 text-xs">No hay eventos registrados que coincidan con el criterio seleccionado.</td></tr>`;
                }

                return filteredLogs.map(l => {
                    let evBadge = "";
                    const tev = l.tipo_evento || "";
                    if (tev === "USER_LOGIN") evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-blue-100 text-blue-800 border border-blue-200">USER_LOGIN</span>`;
                    else if (tev === "USER_LOGOUT") evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-slate-100 text-slate-700 border border-slate-200">USER_LOGOUT</span>`;
                    else if (tev === "LOGIN_FAILED" || tev === "LOGIN_BLOCKED") evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-red-100 text-red-800 border border-red-300">${tev}</span>`;
                    else if (tev === "DEPLOY_REQUESTED" || tev === "REQUEST_CREATE") evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-purple-100 text-purple-800 border border-purple-200">SOLICITUD</span>`;
                    else if (tev === "SLICE_DEPLOYED") evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-emerald-100 text-emerald-800 border border-emerald-300">DESPLIEGUE</span>`;
                    else if (tev === "SLICE_STOPPED") evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-amber-100 text-amber-800 border border-amber-300">DETENCIÓN</span>`;
                    else if (tev === "SLICE_DELETED" || tev === "ADMIN_INTERVENTION") evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-bold bg-orange-100 text-orange-900 border border-orange-300">${tev}</span>`;
                    else if (tev === "SECURITY_PURGE" || tev === "SECURITY_ALERT") evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-black bg-red-700 text-white">${tev}</span>`;
                    else evBadge = `<span class="px-2 py-0.5 rounded text-[10px] font-semibold bg-slate-100 text-slate-800">${tev}</span>`;

                    let sevBadge = "";
                    if (l.severidad === "CRITICAL") sevBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-black bg-red-600 text-white">CRITICAL</span>`;
                    else if (l.severidad === "WARNING") sevBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-amber-100 text-amber-900 border border-amber-300">WARNING</span>`;
                    else sevBadge = `<span class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-100 text-slate-600">INFO</span>`;

                    return `
                        <tr class="border-b border-slate-100 hover:bg-slate-50/80 text-xs">
                            <td class="p-3 font-mono text-slate-600 whitespace-nowrap text-[11px]">
                                ${new Date(l.timestamp).toLocaleString()}
                            </td>
                            <td class="p-3">${evBadge}</td>
                            <td class="p-3">${sevBadge}</td>
                            <td class="p-3 font-mono font-bold text-slate-900">
                                ${l.codigo_usuario || (l.usuario_id ? `#${l.usuario_id}` : 'Sistema')}
                            </td>
                            <td class="p-3 font-mono text-blue-900 text-[11px]">
                                ${l.entidad ? `${l.entidad} ${l.entidad_id ? `#${l.entidad_id}` : ''}` : '-'}
                            </td>
                            <td class="p-3 text-slate-700 max-w-md">
                                <span class="break-words font-medium">${l.descripcion}</span>
                            </td>
                            <td class="p-3 font-mono text-slate-400 text-[10px]">
                                ${l.ip_origen || '127.0.0.1'}
                            </td>
                        </tr>
                    `;
                }).join("");
            };

            container.innerHTML = `
                <div class="space-y-6">
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                        <div>
                            <h3 class="text-sm font-bold text-slate-900">Auditoría Global, Registro de Eventos y Seguridad</h3>
                            <p class="text-xs text-slate-500">Trazabilidad exhaustiva a tiempo completo de accesos, aprovisionamientos, detenciones, purgas e incidentes.</p>
                        </div>
                        <div class="flex items-center space-x-2">
                            <button type="button" id="btn-admin-purge-custom" class="bg-red-700 hover:bg-red-800 text-white px-3 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition shadow-sm">
                                🚨 PURGA POR INCIDENTE
                            </button>
                            <button type="button" id="btn-admin-refresh-audit" class="bg-blue-900 hover:bg-blue-950 text-white px-3 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition shadow-sm">
                                ⟳ ACTUALIZAR LOGS
                            </button>
                        </div>
                    </div>

                    ${anomalyHtml}
                    ${countersHtml}

                    <!-- Filtros y Búsqueda -->
                    <div class="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 space-y-4">
                        <div class="grid grid-cols-1 md:grid-cols-3 gap-3">
                            <div>
                                <label class="block text-[11px] font-bold text-slate-600 uppercase mb-1">Filtrar por Tipo de Evento</label>
                                <select id="audit-filter-type" class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2 text-xs text-slate-800 font-semibold focus:outline-none focus:ring-2 focus:ring-blue-900">
                                    <option value="ALL">Todos los Eventos</option>
                                    <option value="USER_LOGIN">USER_LOGIN (Inicios de Sesión)</option>
                                    <option value="LOGIN_FAILED">LOGIN_FAILED (Intentos Fallidos)</option>
                                    <option value="USER_CREATED">USER_CREATED (Nuevas Cuentas)</option>
                                    <option value="USER_MODIFIED">USER_MODIFIED (Cambios de Rol/Nivel)</option>
                                    <option value="DEPLOY_REQUESTED">DEPLOY_REQUESTED (Solicitudes)</option>
                                    <option value="SLICE_DEPLOYED">SLICE_DEPLOYED (Despliegues Activos)</option>
                                    <option value="SLICE_STOPPED">SLICE_STOPPED (Detenciones)</option>
                                    <option value="SLICE_DELETED">SLICE_DELETED (Eliminaciones)</option>
                                    <option value="ADMIN_INTERVENTION">ADMIN_INTERVENTION (Intervenciones)</option>
                                    <option value="SECURITY_PURGE">SECURITY_PURGE (Purgas de Seguridad)</option>
                                </select>
                            </div>

                            <div>
                                <label class="block text-[11px] font-bold text-slate-600 uppercase mb-1">Filtrar por Severidad</label>
                                <select id="audit-filter-severity" class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2 text-xs text-slate-800 font-semibold focus:outline-none focus:ring-2 focus:ring-blue-900">
                                    <option value="ALL">Todas las Severidades</option>
                                    <option value="INFO">INFO (Operaciones Rutinarias)</option>
                                    <option value="WARNING">WARNING (Detenciones / Fallos Menores)</option>
                                    <option value="CRITICAL">CRITICAL (Alertas / Purgas / Bloqueos)</option>
                                </select>
                            </div>

                            <div>
                                <label class="block text-[11px] font-bold text-slate-600 uppercase mb-1">Buscar en Logs</label>
                                <input type="text" id="audit-filter-search" placeholder="Buscar por usuario, slice o texto..." class="w-full bg-slate-50 border border-slate-300 rounded-lg p-2 text-xs text-slate-800 focus:outline-none focus:ring-2 focus:ring-blue-900">
                            </div>
                        </div>

                        <!-- Tabla de Logs -->
                        <div class="overflow-x-auto rounded-xl border border-slate-200">
                            <table class="w-full text-left border-collapse">
                                <thead>
                                    <tr class="bg-slate-50 border-b border-slate-200 text-slate-600 text-[10px] font-bold uppercase tracking-wider">
                                        <th class="p-3">Fecha y Hora</th>
                                        <th class="p-3">Evento</th>
                                        <th class="p-3">Severidad</th>
                                        <th class="p-3">Usuario</th>
                                        <th class="p-3">Entidad</th>
                                        <th class="p-3">Detalle / Descripción del Evento</th>
                                        <th class="p-3">IP Origen</th>
                                    </tr>
                                </thead>
                                <tbody id="audit-table-body">
                                    ${renderLogsTable(adminAuditLogsList)}
                                </tbody>
                            </table>
                        </div>
                    </div>
                </div>
            `;

            // Event Listeners para Filtros en tiempo real
            const updateFilter = () => {
                const tval = document.getElementById("audit-filter-type").value;
                const sval = document.getElementById("audit-filter-severity").value;
                const qval = document.getElementById("audit-filter-search").value.toLowerCase().trim();

                const filtered = adminAuditLogsList.filter(l => {
                    const matchType = tval === "ALL" || l.tipo_evento === tval;
                    const matchSev = sval === "ALL" || l.severidad === sval;
                    const matchQ = !qval || 
                        (l.codigo_usuario && l.codigo_usuario.toLowerCase().includes(qval)) ||
                        (l.descripcion && l.descripcion.toLowerCase().includes(qval)) ||
                        (l.entidad_id && l.entidad_id.toString().includes(qval));
                    return matchType && matchSev && matchQ;
                });

                document.getElementById("audit-table-body").innerHTML = renderLogsTable(filtered);
            };

            document.getElementById("audit-filter-type").onchange = updateFilter;
            document.getElementById("audit-filter-severity").onchange = updateFilter;
            document.getElementById("audit-filter-search").oninput = updateFilter;

            // Botón actualizar
            document.getElementById("btn-admin-refresh-audit").onclick = () => AdminView.loadAudit(containerId);

            // Botón purga personalizada
            document.getElementById("btn-admin-purge-custom").onclick = () => {
                const activeSlices = adminSlicesList.filter(s => s.estado !== "ELIMINATED");
                if (!activeSlices.length) {
                    alert("No hay slices activos para purgar.");
                    return;
                }
                const sl = activeSlices[0];
                AdminView.openSecurityPurgeModal(sl.id, sl.nombre);
            };

            // Botones de purga en tarjetas de anomalías
            container.querySelectorAll(".btn-trigger-purge").forEach(btn => {
                btn.onclick = () => {
                    AdminView.openSecurityPurgeModal(btn.dataset.sliceid, btn.dataset.slicename);
                };
            });

        } catch (e) {
            container.innerHTML = `
                <div class="p-6 bg-red-50 border border-red-200 text-red-700 rounded-xl text-sm">
                    Error al cargar módulo de auditoría y seguridad: ${e.message}
                </div>
            `;
        }
    },

    openSecurityPurgeModal: (sliceId, sliceName) => {
        const modal = document.getElementById("modal-admin-security-purge");
        if (!modal) return;
        document.getElementById("purge-slice-id-input").value = sliceId;
        document.getElementById("purge-slice-display").innerText = `#${sliceId} — ${sliceName}`;
        document.getElementById("purge-incident-reason").value = "";
        modal.classList.remove("hidden");

        const btnConfirm = document.getElementById("btn-confirm-security-purge");
        btnConfirm.onclick = async () => {
            const motivo = document.getElementById("purge-incident-reason").value.trim();
            if (!motivo || motivo.length < 5) {
                alert("Por norma de seguridad, debe ingresar una justificación documentada para la purga.");
                return;
            }

            btnConfirm.disabled = true;
            btnConfirm.innerText = "PURGANDO...";

            try {
                await API.admin.purgeSliceSecurity(sliceId, motivo);
                alert(`Purga de seguridad completada con éxito. Slice #${sliceId} ha sido eliminado forzosamente y todos los recursos del hipervisor liberados.`);
                modal.classList.add("hidden");
                AdminView.loadAudit("admin-audit-container");
            } catch (err) {
                alert(`Error en purga de seguridad: ${err.message}`);
            } finally {
                btnConfirm.disabled = false;
                btnConfirm.innerText = "PURGAR Y ELIMINAR RECURSOS";
            }
        };
    }
};

window.AdminView = AdminView;
