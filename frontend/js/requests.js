/**
 * requests.js — Bandeja de Solicitudes, Revisión Técnica de Topología y Decisiones
 * Orquestador Cloud TEL141 - Grupo 3
 */

import { API, Storage } from "./api.js";
import { Wizard } from "./wizard.js";
import { TopologyViewer } from "./topology.js";

let requestsList = [];
let reviewCyInstance = null;

export const Requests = {
    load: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="p-8 text-center text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Cargando historial de solicitudes...
            </div>
        `;

        try {
            requestsList = await API.query.getRequests();
            Requests.render(container);
        } catch (e) {
            container.innerHTML = `
                <div class="p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                    Error al cargar solicitudes: ${e.message}
                </div>
            `;
        }
    },

    getBadge: (estado) => {
        switch (estado) {
            case "PENDING":
                return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-amber-100 text-amber-800 border border-amber-300">EN REVISIÓN</span>`;
            case "APPROVED":
                return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-emerald-100 text-emerald-800 border border-emerald-300">APROBADO</span>`;
            case "REJECTED_BY_OPERATOR":
            case "REJECTED_BY_SYSTEM":
                return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-red-100 text-red-800 border border-red-300">RECHAZADO</span>`;
            case "VALIDATING":
                return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-sky-100 text-sky-800 border border-sky-300">VALIDANDO</span>`;
            default:
                return `<span class="px-2.5 py-0.5 rounded-full text-xs font-bold bg-slate-100 text-slate-700">${estado}</span>`;
        }
    },

    render: (container) => {
        if (!requestsList || requestsList.length === 0) {
            container.innerHTML = `
                <div class="p-12 text-center bg-white rounded-xl border border-slate-200 shadow-sm">
                    <div class="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center mx-auto mb-3 text-slate-400 font-bold text-xl">
                        📋
                    </div>
                    <h3 class="text-sm font-semibold text-slate-800 mb-1">Sin solicitudes registradas</h3>
                    <p class="text-xs text-slate-500 mb-4 max-w-sm mx-auto">
                        Cuando se envíen solicitudes de despliegue a la plataforma, aquí podrás consultar su contenido, topología y estado de aprobación.
                    </p>
                </div>
            `;
            return;
        }

        const currentUser = Storage.getUser();
        const isStaff = currentUser?.rol === "operador" || currentUser?.rol === "admin";

        const rows = requestsList.map(r => {
            const dateStr = r.fecha_sol ? new Date(r.fecha_sol).toLocaleString() : "Reciente";
            const isRejected = r.estado.includes("REJECTED");
            const isPending = r.estado === "PENDING";

            return `
                <tr class="border-b border-slate-100 hover:bg-slate-50 text-xs">
                    <td class="p-3 font-semibold text-slate-900">${r.slice_nombre || `Slice #${r.slice_id}`}</td>
                    ${isStaff ? `<td class="p-3 font-mono font-semibold text-blue-950">${r.usuario_codigo || `Usuario #${r.usuario_id}`}</td>` : ''}
                    <td class="p-3 text-slate-600">${r.tipo_deployment || 'DEPLOY'}</td>
                    <td class="p-3 text-slate-500">${dateStr}</td>
                    <td class="p-3">
                        ${Requests.getBadge(r.estado)}
                    </td>
                    <td class="p-3 text-slate-600">
                        ${r.operador_id ? `Operador #${r.operador_id}` : (r.estado === 'APPROVED' ? 'Sistema Automático' : 'Pendiente')}
                    </td>
                    <td class="p-3 text-right space-x-1 whitespace-nowrap">
                        <button data-id="${r.id}" class="btn-req-review bg-blue-900 text-white hover:bg-blue-950 px-2.5 py-1 rounded font-semibold text-xs" title="Ver contenido, VMs, enlaces y topología">
                            ${isStaff ? 'REVISAR SOLICITUD' : 'VER DETALLES'}
                        </button>
                        ${isStaff && isPending ? `
                            <button data-id="${r.id}" data-deploymentid="${r.deployment_id}" class="btn-req-approve bg-emerald-700 text-white hover:bg-emerald-800 px-2.5 py-1 rounded font-semibold text-xs" title="Aprobar despliegue">
                                APROBAR
                            </button>
                            <button data-id="${r.id}" data-deploymentid="${r.deployment_id}" class="btn-req-reject bg-red-600 text-white hover:bg-red-700 px-2.5 py-1 rounded font-semibold text-xs" title="Rechazar con motivo">
                                RECHAZAR
                            </button>
                        ` : ''}
                        ${isRejected ? `
                            <button data-motivo="${r.motivo || 'Sin motivo especificado'}" class="btn-req-reason bg-red-50 text-red-700 hover:bg-red-100 px-2.5 py-1 rounded font-semibold text-xs">
                                VER MOTIVO
                            </button>
                        ` : ''}
                        ${!isStaff ? `
                            <button data-sliceid="${r.slice_id}" class="btn-req-clone bg-slate-100 text-slate-700 hover:bg-slate-200 px-2.5 py-1 rounded font-semibold text-xs" title="Clonar a un nuevo borrador editable">
                                CREAR BORRADOR DESDE ESTE
                            </button>
                        ` : ''}
                    </td>
                </tr>
            `;
        }).join("");

        container.innerHTML = `
            <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs font-semibold uppercase tracking-wider">
                            <th class="p-3">Slice</th>
                            ${isStaff ? `<th class="p-3">Solicitante</th>` : ''}
                            <th class="p-3">Operación</th>
                            <th class="p-3">Fecha</th>
                            <th class="p-3">Estado</th>
                            <th class="p-3">Evaluador</th>
                            <th class="p-3 text-right">Acciones</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${rows}
                    </tbody>
                </table>
            </div>
        `;

        Requests.bindEvents(container);
    },

    bindEvents: (container) => {
        // 1. Abrir Modal de Revisión Completa (Contenido + Topología + Decisión)
        container.querySelectorAll(".btn-req-review").forEach(btn => {
            btn.addEventListener("click", () => {
                const id = parseInt(btn.dataset.id, 10);
                const req = requestsList.find(x => x.id === id);
                if (req) Requests.openReviewModal(req);
            });
        });

        // 2. Ver motivo de rechazo (modal rápido)
        container.querySelectorAll(".btn-req-reason").forEach(btn => {
            btn.addEventListener("click", () => {
                const motivo = btn.dataset.motivo;
                alert(`Motivo registrado por el Operador:\n\n"${motivo}"`);
            });
        });

        // 3. Aprobar solicitud rápida desde la tabla (Operador / Admin)
        container.querySelectorAll(".btn-req-approve").forEach(btn => {
            btn.addEventListener("click", async () => {
                const deploymentId = parseInt(btn.dataset.deploymentid, 10);
                if (!confirm("¿Deseas aprobar esta solicitud de despliegue?")) return;
                try {
                    await API.slices.approveDeployment(deploymentId, true);
                    alert("Solicitud aprobada exitosamente. El orquestador continuará el aprovisionamiento.");
                    await Requests.load(container.id);
                    window.dispatchEvent(new CustomEvent("request:updated"));
                } catch (e) {
                    alert(`Error al aprobar solicitud: ${e.message}`);
                }
            });
        });

        // 4. Rechazar solicitud rápida desde la tabla (Operador / Admin)
        container.querySelectorAll(".btn-req-reject").forEach(btn => {
            btn.addEventListener("click", async () => {
                const deploymentId = parseInt(btn.dataset.deploymentid, 10);
                const reason = prompt("Ingrese el motivo técnico del rechazo (obligatorio):", "Políticas de capacidad o mantenimiento de infraestructura");
                if (reason === null) return;
                if (!reason.trim()) {
                    alert("El motivo de rechazo es obligatorio para sustentar la decisión ante el consumidor.");
                    return;
                }
                try {
                    await API.slices.approveDeployment(deploymentId, false, reason.trim());
                    alert("Solicitud rechazada exitosamente. El motivo ha sido registrado.");
                    await Requests.load(container.id);
                    window.dispatchEvent(new CustomEvent("request:updated"));
                } catch (e) {
                    alert(`Error al rechazar solicitud: ${e.message}`);
                }
            });
        });

        // 5. Crear borrador desde este (Reintentar con cambios para consumidor)
        container.querySelectorAll(".btn-req-clone").forEach(btn => {
            btn.addEventListener("click", async () => {
                const sliceId = parseInt(btn.dataset.sliceid, 10);
                try {
                    const top = await API.query.getSliceTopology(sliceId);
                    Wizard.open({
                        nombre: `${top.nombre || 'slice'}-reintento`,
                        zona_id: top.zona_id || 1,
                        spec: top.spec
                    }, () => {
                        window.dispatchEvent(new CustomEvent("draft:created"));
                    });
                } catch (e) {
                    alert(`Error al clonar solicitud: ${e.message}`);
                }
            });
        });

        // Eventos de cierre del modal de revisión
        const closeBtn = document.getElementById("review-modal-close-btn");
        const closeBottomBtn = document.getElementById("review-modal-close-bottom-btn");
        if (closeBtn) closeBtn.onclick = () => Requests.closeReviewModal();
        if (closeBottomBtn) closeBottomBtn.onclick = () => Requests.closeReviewModal();
    },

    openReviewModal: async (req) => {
        const modal = document.getElementById("modal-request-review");
        if (!modal) return;

        modal.classList.remove("hidden");

        // Datos iniciales
        document.getElementById("review-modal-title").innerText = `Revisión Técnica — ${req.slice_nombre || 'Slice #' + req.slice_id}`;
        document.getElementById("review-slice-name").innerText = req.slice_nombre || `Slice #${req.slice_id}`;
        document.getElementById("review-user-code").innerText = req.usuario_codigo || `Usuario #${req.usuario_id}`;
        document.getElementById("review-op-type").innerText = req.tipo_deployment || "DEPLOY";
        document.getElementById("review-date").innerText = req.fecha_sol ? new Date(req.fecha_sol).toLocaleString() : "-";
        
        const badgeEl = document.getElementById("review-modal-badge");
        if (badgeEl) badgeEl.innerHTML = Requests.getBadge(req.estado);

        // Feedback de carga en el lienzo de topología
        const cyContainer = document.getElementById("review-cytoscape-container");
        cyContainer.innerHTML = `
            <div class="flex items-center justify-center h-full text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Cargando topología y especificación...
            </div>
        `;

        try {
            const [topology, deployment] = await Promise.all([
                API.query.getSliceTopology(req.slice_id).catch(() => null),
                API.query.getDeployment(req.deployment_id).catch(() => null)
            ]);

            const spec = topology?.spec || {};
            const vms = spec.vms || [];
            const links = spec.links || [];

            // Zona y VLAN
            document.getElementById("review-zone").innerText = topology?.zona_nombre || (topology?.zona_id ? `Zona ${topology.zona_id}` : "linx-1");
            document.getElementById("review-vlan").innerText = topology?.vlan_stag ? `VLAN ${topology.vlan_stag}` : "Sin VLAN";

            // Balance de Recursos Totales
            let totalVcpu = 0;
            let totalRamMb = 0;
            let totalDiskMb = 0;

            if (deployment?.plan?.summary?.resource_delta) {
                totalVcpu = deployment.plan.summary.resource_delta.vcpu || 0;
                totalRamMb = deployment.plan.summary.resource_delta.ram_mb || 0;
                totalDiskMb = deployment.plan.summary.resource_delta.disk_mb || 0;
            } else if (deployment?.plan?.summary?.resource_total_after) {
                totalVcpu = deployment.plan.summary.resource_total_after.vcpu || 0;
                totalRamMb = deployment.plan.summary.resource_total_after.ram_mb || 0;
                totalDiskMb = deployment.plan.summary.resource_total_after.disk_mb || 0;
            } else {
                vms.forEach(vm => {
                    const f = (vm.flavor || "micro-v1").toLowerCase();
                    if (f.includes("large")) { totalVcpu += 4; totalRamMb += 4096; totalDiskMb += 10000; }
                    else if (f.includes("medium")) { totalVcpu += 2; totalRamMb += 2048; totalDiskMb += 5000; }
                    else if (f.includes("small")) { totalVcpu += 1; totalRamMb += 1024; totalDiskMb += 2000; }
                    else { totalVcpu += 1; totalRamMb += 256; totalDiskMb += 1000; }
                });
            }

            document.getElementById("review-res-vcpu").innerText = totalVcpu;
            document.getElementById("review-res-ram").innerText = totalRamMb >= 1024 ? `${(totalRamMb / 1024).toFixed(1)} GB` : `${totalRamMb} MB`;
            document.getElementById("review-res-disk").innerText = totalDiskMb >= 1000 ? `${(totalDiskMb / 1000).toFixed(1)} GB` : `${totalDiskMb} MB`;
            document.getElementById("review-count-vms").innerText = vms.length;
            document.getElementById("review-count-links").innerText = links.length;

            // Render tabla VMs
            const vmsTbody = document.getElementById("review-vms-table-body");
            if (vms.length === 0) {
                vmsTbody.innerHTML = `<tr><td colspan="4" class="p-3 text-center text-slate-400">Sin VMs especificadas</td></tr>`;
            } else {
                vmsTbody.innerHTML = vms.map(vm => `
                    <tr class="border-b border-slate-100 hover:bg-slate-50">
                        <td class="p-2 font-semibold text-slate-900">${vm.name}</td>
                        <td class="p-2 font-mono text-slate-700">${vm.flavor || 'micro-v1'}</td>
                        <td class="p-2 text-slate-600">${vm.image || 'Ubuntu'}</td>
                        <td class="p-2 text-center">
                            ${vm.public 
                                ? '<span class="px-1.5 py-0.5 rounded font-bold bg-emerald-100 text-emerald-800 text-[10px]">SÍ</span>' 
                                : '<span class="px-1.5 py-0.5 rounded text-slate-400 text-[10px]">NO</span>'}
                        </td>
                    </tr>
                `).join("");
            }

            // Render tabla Links
            const linksTbody = document.getElementById("review-links-table-body");
            if (links.length === 0) {
                linksTbody.innerHTML = `<tr><td colspan="2" class="p-3 text-center text-slate-400">Sin enlaces de red</td></tr>`;
            } else {
                linksTbody.innerHTML = links.map(l => {
                    const ep = l.endpoints || l.nodes || [];
                    return `
                        <tr class="border-b border-slate-100 hover:bg-slate-50">
                            <td class="p-2 font-semibold text-slate-900">${l.name}</td>
                            <td class="p-2 font-mono text-blue-900">${ep.join(' ↔ ')}</td>
                        </tr>
                    `;
                }).join("");
            }

            // Render Cytoscape
            cyContainer.innerHTML = "";
            if (reviewCyInstance) {
                try { reviewCyInstance.destroy(); } catch (e) {}
                reviewCyInstance = null;
            }

            reviewCyInstance = TopologyViewer.render({
                container: cyContainer,
                spec: spec,
                topologyType: "anillo",
                showNetworkDetails: true,
                isLive: false
            });

            setTimeout(() => {
                if (reviewCyInstance) {
                    reviewCyInstance.resize();
                    reviewCyInstance.fit(undefined, 25);
                }
            }, 150);

            // Sección de resolución histórica (si ya fue evaluada)
            const secEval = document.getElementById("review-evaluated-section");
            const txtEval = document.getElementById("review-evaluated-text");
            if (req.estado !== "PENDING") {
                secEval.classList.remove("hidden");
                const opText = req.operador_id ? `Evaluado por el Operador #${req.operador_id}` : `Aprobado por el Sistema Automático`;
                const motivoText = req.motivo ? `\nMotivo / Justificación registrada: "${req.motivo}"` : "";
                txtEval.innerText = `${opText} el ${req.fecha_sol ? new Date(req.fecha_sol).toLocaleString() : ''}. Estado final: ${req.estado}.${motivoText}`;
            } else {
                secEval.classList.add("hidden");
            }

            // Panel de decisión del Operador (solo visible para Operador/Admin en estado PENDING)
            const currentUser = Storage.getUser();
            const isStaff = currentUser?.rol === "operador" || currentUser?.rol === "admin";
            const opPanel = document.getElementById("review-operator-decision-panel");

            if (isStaff && req.estado === "PENDING") {
                opPanel.classList.remove("hidden");
                const reasonInput = document.getElementById("review-decision-reason");
                if (reasonInput) reasonInput.value = "";

                const btnApprove = document.getElementById("btn-review-approve");
                const btnReject = document.getElementById("btn-review-reject");

                btnApprove.onclick = async () => {
                    if (!confirm(`¿Confirmas la APROBACIÓN del despliegue para ${req.slice_nombre}?`)) return;
                    try {
                        btnApprove.disabled = true;
                        btnApprove.innerText = "Aprobando...";
                        await API.slices.approveDeployment(req.deployment_id, true);
                        alert("¡Solicitud Aprobada con éxito! El orquestador iniciará el aprovisionamiento en los hipervisores.");
                        Requests.closeReviewModal();
                        await Requests.load("requests-table-container");
                        window.dispatchEvent(new CustomEvent("request:updated"));
                    } catch (err) {
                        alert(`Error al aprobar: ${err.message}`);
                    } finally {
                        btnApprove.disabled = false;
                        btnApprove.innerText = "APROBAR SOLICITUD";
                    }
                };

                btnReject.onclick = async () => {
                    const reason = (reasonInput?.value || "").trim();
                    if (!reason) {
                        alert("Debes ingresar obligatoriamente un motivo u observaciones técnicas para sustentar el rechazo.");
                        reasonInput?.focus();
                        return;
                    }
                    if (!confirm(`¿Confirmas el RECHAZO de la solicitud de ${req.slice_nombre}?`)) return;
                    try {
                        btnReject.disabled = true;
                        btnReject.innerText = "Rechazando...";
                        await API.slices.approveDeployment(req.deployment_id, false, reason);
                        alert("Solicitud rechazada. El motivo ha sido registrado para el consumidor.");
                        Requests.closeReviewModal();
                        await Requests.load("requests-table-container");
                        window.dispatchEvent(new CustomEvent("request:updated"));
                    } catch (err) {
                        alert(`Error al rechazar: ${err.message}`);
                    } finally {
                        btnReject.disabled = false;
                        btnReject.innerText = "RECHAZAR SOLICITUD";
                    }
                };
            } else {
                opPanel.classList.add("hidden");
            }

        } catch (err) {
            cyContainer.innerHTML = `<div class="p-8 text-center text-red-600">Error al cargar detalles de la solicitud: ${err.message}</div>`;
        }
    },

    closeReviewModal: () => {
        const modal = document.getElementById("modal-request-review");
        if (modal) modal.classList.add("hidden");
        if (reviewCyInstance) {
            try { reviewCyInstance.destroy(); } catch (e) {}
            reviewCyInstance = null;
        }
    }
};
