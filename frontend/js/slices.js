/**
 * slices.js — Gestión de Slices Activos, Visor de Topología y Datos VNC
 * Orquestador Cloud TEL141 - Grupo 3
 */

import { API, Storage } from "./api.js";
import { TopologyViewer } from "./topology.js";

let activeSlices = [];
let liveCyInstance = null;
let currentTopologyData = null;
let showDetailsState = true;

export const Slices = {
    load: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="p-8 text-center text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Cargando slices activos...
            </div>
        `;

        try {
            const all = await API.query.getSlices();
            // Filtrar los que no son borradores ni eliminados
            activeSlices = (all || []).filter(s => s.estado === "RUNNING" && s.version_activa_id !== null);
            Slices.render(container);
        } catch (e) {
            container.innerHTML = `
                <div class="p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                    Error al cargar slices: ${e.message}
                </div>
            `;
        }
    },

    render: (container) => {
        if (!activeSlices || activeSlices.length === 0) {
            container.innerHTML = `
                <div class="p-12 text-center bg-white rounded-xl border border-slate-200 shadow-sm">
                    <div class="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center mx-auto mb-3 text-slate-400 font-bold text-xl">
                        ☁
                    </div>
                    <h3 class="text-sm font-semibold text-slate-800 mb-1">No hay slices desplegados en este momento</h3>
                    <p class="text-xs text-slate-500 mb-4 max-w-sm mx-auto">
                        Tus slices activos en la infraestructura aparecerán aquí tras ser aprobados y desplegados por el orquestador.
                    </p>
                </div>
            `;
            return;
        }

        const getBadge = (estado) => {
            switch (estado) {
                case "RUNNING":
                    return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 border border-emerald-300">● ACTIVO</span>`;
                case "UPDATING":
                    return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-indigo-100 text-indigo-800 border border-indigo-300">ACTUALIZANDO</span>`;
                case "STOPPED":
                    return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-slate-100 text-slate-700 border border-slate-300">DETENIDO</span>`;
                case "FAILED":
                    return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-800 border border-red-300">FALLIDO</span>`;
                default:
                    return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-slate-100 text-slate-700">${estado}</span>`;
            }
        };

        const currentUser = Storage.getUser();
        const isConsumer = currentUser?.rol === "consumidor";

        const rows = activeSlices.map(s => `
            <tr class="border-b border-slate-100 hover:bg-slate-50 text-xs">
                <td class="p-3 font-semibold text-slate-900">${s.nombre}</td>
                <td class="p-3 text-slate-600">${s.zona_nombre || `Zona ${s.zona_id}`}</td>
                <td class="p-3 text-slate-600">
                    ${s.vlan_stag ? `<span class="font-mono bg-slate-100 px-1.5 py-0.5 rounded">VLAN ${s.vlan_stag}</span>` : 'Sin VLAN'}
                </td>
                <td class="p-3">
                    ${getBadge(s.estado)}
                </td>
                <td class="p-3 text-right space-x-1">
                    <button data-id="${s.id}" class="btn-slice-topo bg-blue-900 text-white hover:bg-blue-950 px-2.5 py-1 rounded font-semibold text-xs">
                        TOPOLOGÍA
                    </button>
                    ${isConsumer ? `
                        <button data-id="${s.id}" class="btn-slice-newver bg-slate-100 text-slate-700 hover:bg-slate-200 px-2.5 py-1 rounded font-semibold text-xs" title="Crear nueva versión para editar">
                            NUEVA VERSIÓN
                        </button>
                    ` : ''}
                </td>
            </tr>
        `).join("");

        container.innerHTML = `
            <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs font-semibold uppercase tracking-wider">
                            <th class="p-3">Nombre</th>
                            <th class="p-3">Zona</th>
                            <th class="p-3">Red (VLAN)</th>
                            <th class="p-3">Estado</th>
                            <th class="p-3 text-right">Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rows}
                    </tbody>
                </table>
            </div>
        `;

        Slices.bindEvents(container);
    },

    bindEvents: (container) => {
        // Abrir visor de Topología Activa
        container.querySelectorAll(".btn-slice-topo").forEach(btn => {
            btn.addEventListener("click", () => {
                const id = parseInt(btn.dataset.id, 10);
                Slices.openTopologyModal(id);
            });
        });

        // Crear nueva versión (Editar en caliente)
        container.querySelectorAll(".btn-slice-newver").forEach(btn => {
            btn.addEventListener("click", async () => {
                const id = parseInt(btn.dataset.id, 10);
                if (!confirm("¿Deseas crear una nueva versión de este slice para editar su diseño sin destruirlo?")) return;
                try {
                    await API.slices.createVersion(id);
                    alert("Nueva versión borrador creada. La encontrarás disponible en la pestaña 'Mis Borradores'.");
                    window.dispatchEvent(new CustomEvent("slice:new_version"));
                } catch (e) {
                    alert(`Error al crear nueva versión: ${e.message}`);
                }
            });
        });

        // Destruir Slice
        container.querySelectorAll(".btn-slice-destroy").forEach(btn => {
            btn.addEventListener("click", async () => {
                const id = parseInt(btn.dataset.id, 10);
                if (!confirm("¿CONFIRMAS LA DESTRUCCIÓN DE ESTE SLICE? Esta acción liberará todas las VMs, VLANs y recursos asignados.")) return;
                try {
                    await API.slices.destroy(id);
                    alert("Solicitud de destrucción enviada a Temporal. Los recursos se están liberando.");
                    Slices.load(container.id);
                } catch (e) {
                    alert(`Error al destruir slice: ${e.message}`);
                }
            });
        });
    },

    openTopologyModal: async (sliceId) => {
        const modal = document.getElementById("topology-modal");
        if (!modal) return;

        modal.classList.remove("hidden");
        const container = document.getElementById("active-cytoscape-container");
        container.innerHTML = `
            <div class="h-full flex items-center justify-center text-slate-400 text-sm">
                Cargando topología y servidores físicos...
            </div>
        `;

        try {
            const [top, servers] = await Promise.all([
                API.query.getSliceTopology(sliceId),
                API.query.getServers().catch(() => [])
            ]);

            currentTopologyData = top;

            document.getElementById("topo-modal-title").innerText = `Topología en Vivo: ${top.nombre} (VLAN ${top.vlan_stag || 'N/A'})`;
            
            // Cerrar drawer lateral
            const drawer = document.getElementById("node-detail-drawer");
            if (drawer) drawer.classList.add("hidden");

            liveCyInstance = TopologyViewer.render({
                container,
                spec: { vms: top.nodos, links: top.enlaces },
                topologyType: "cose",
                showNetworkDetails: showDetailsState,
                isLive: true,
                servers: servers || [],
                onNodeSelect: (vm) => {
                    Slices.showNodeDrawer(vm);
                }
            });

            // Botón Exportar PNG
            const btnPng = document.getElementById("topo-export-png");
            if (btnPng) {
                btnPng.onclick = () => {
                    const pngData = TopologyViewer.exportPng(liveCyInstance);
                    if (pngData) {
                        const a = document.createElement("a");
                        a.href = pngData;
                        a.download = `topologia-${top.nombre}.png`;
                        a.click();
                    }
                };
            }

            // Interruptor de Detalles de Red
            const chkDetails = document.getElementById("topo-toggle-details");
            if (chkDetails) {
                chkDetails.checked = showDetailsState;
                chkDetails.onchange = (e) => {
                    showDetailsState = e.target.checked;
                    Slices.openTopologyModal(sliceId);
                };
            }

        } catch (e) {
            container.innerHTML = `
                <div class="p-6 text-red-600 text-sm">
                    Error al cargar topología: ${e.message}
                </div>
            `;
        }
    },

    closeTopologyModal: () => {
        const modal = document.getElementById("topology-modal");
        if (modal) modal.classList.add("hidden");
        if (liveCyInstance) {
            liveCyInstance.destroy();
            liveCyInstance = null;
        }
    },

    showNodeDrawer: (vm) => {
        const drawer = document.getElementById("node-detail-drawer");
        if (!drawer) return;

        if (!vm) {
            drawer.classList.add("hidden");
            return;
        }

        drawer.classList.remove("hidden");

        const vncPort = vm.vnc_display !== null && vm.vnc_display !== undefined 
            ? 5900 + vm.vnc_display 
            : (vm.puerto_vnc || 'No asignado');

        const hostIp = vm.servidor_ip || "Asignando...";

        drawer.innerHTML = `
            <div class="p-4 border-b border-slate-200 flex items-center justify-between">
                <div>
                    <h4 class="font-bold text-slate-900 text-sm">${vm.name}</h4>
                    <span class="text-xs text-emerald-700 font-semibold">● ${vm.estado_nodo || 'ACTIVO'}</span>
                </div>
                <button id="btn-close-drawer" class="text-slate-400 hover:text-slate-700 font-bold text-lg">&times;</button>
            </div>
            
            <div class="p-4 space-y-3 text-xs">
                <div>
                    <span class="block text-slate-500 font-medium">Especificación de Cómputo:</span>
                    <span class="font-semibold text-slate-800">${vm.flavor_name || vm.flavor || 'Sabor base'} (${vm.vcpu || 1} vCPU, ${vm.vram_mb || 256} MB RAM)</span>
                </div>

                <div>
                    <span class="block text-slate-500 font-medium">Imagen del Sistema:</span>
                    <span class="font-semibold text-slate-800">${vm.imagen_nombre || vm.image || 'Ubuntu'}</span>
                </div>

                <div>
                    <span class="block text-slate-500 font-medium">Servidor Hipervisor Host:</span>
                    <span class="font-semibold text-slate-800 font-mono">${hostIp} ${vm.servidor_id ? `(ID: ${vm.servidor_id})` : ''}</span>
                </div>

                <!-- Tarjeta Destacada de Consola VNC -->
                <div class="p-3 bg-slate-900 text-white rounded-lg">
                    <span class="block text-slate-400 font-semibold mb-1 uppercase tracking-wider text-xs">Acceso a Consola Virtual (VNC)</span>
                    <div class="flex items-center justify-between font-mono text-xs">
                        <span>Host: <b>${hostIp}</b></span>
                        <span class="bg-blue-600 px-2 py-0.5 rounded text-white font-bold">Puerto: ${vncPort}</span>
                    </div>
                    <button id="btn-copy-vnc" class="mt-2 w-full bg-slate-800 hover:bg-slate-700 text-slate-200 py-1 rounded text-center text-xs font-semibold">
                        COPIAR DESTINO VNC
                    </button>
                </div>
            </div>
        `;

        const btnClose = document.getElementById("btn-close-drawer");
        if (btnClose) btnClose.onclick = () => drawer.classList.add("hidden");

        const btnCopy = document.getElementById("btn-copy-vnc");
        if (btnCopy) {
            btnCopy.onclick = () => {
                navigator.clipboard.writeText(`${hostIp}:${vncPort}`);
                btnCopy.innerText = "¡COPIADO!";
                setTimeout(() => { btnCopy.innerText = "COPIAR DESTINO VNC"; }, 1500);
            };
        }
    }
};
