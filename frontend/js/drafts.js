/**
 * drafts.js — Gestión de Borradores (Drafts), Importación y Exportación JSON
 * Orquestador Cloud TEL141 - Grupo 3
 */

import { API } from "./api.js";
import { Wizard } from "./wizard.js";

let draftsList = [];

export const Drafts = {
    load: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        container.innerHTML = `
            <div class="p-8 text-center text-slate-400">
                <span class="inline-block animate-spin mr-2">⟳</span> Cargando borradores...
            </div>
        `;

        try {
            draftsList = await API.query.getSlices("DRAFT");
            Drafts.render(container);
        } catch (e) {
            container.innerHTML = `
                <div class="p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                    Error al cargar borradores: ${e.message}
                </div>
            `;
        }
    },

    render: (container) => {
        if (!draftsList || draftsList.length === 0) {
            container.innerHTML = `
                <div class="p-12 text-center bg-white rounded-xl border border-slate-200 shadow-sm">
                    <div class="w-12 h-12 rounded-full bg-slate-100 flex items-center justify-center mx-auto mb-3 text-slate-400 font-bold text-xl">
                        ✎
                    </div>
                    <h3 class="text-sm font-semibold text-slate-800 mb-1">Aún no tienes borradores</h3>
                    <p class="text-xs text-slate-500 mb-4 max-w-sm mx-auto">
                        Crea tu primer diseño de topología usando el wizard asistido o importa un archivo JSON existente.
                    </p>
                    <button id="btn-empty-create" class="bg-blue-900 text-white px-4 py-2 rounded-lg text-xs font-semibold hover:bg-blue-950">
                        CREAR BORRADOR
                    </button>
                </div>
            `;
            const btn = document.getElementById("btn-empty-create");
            if (btn) btn.onclick = () => Wizard.open(null, () => Drafts.load(container.id));
            return;
        }

        const rows = draftsList.map(d => {
            const dateStr = d.fecha_creacion ? new Date(d.fecha_creacion).toLocaleString() : "Reciente";
            return `
                <tr class="border-b border-slate-100 hover:bg-slate-50 text-xs">
                    <td class="p-3 font-semibold text-slate-900">${d.nombre}</td>
                    <td class="p-3 text-slate-600">${d.zona_nombre || `Zona ${d.zona_id}`}</td>
                    <td class="p-3 text-slate-500">${dateStr}</td>
                    <td class="p-3">
                        <span class="px-2 py-0.5 rounded-full text-xs font-semibold bg-slate-100 text-slate-700">
                            BORRADOR
                        </span>
                    </td>
                    <td class="p-3 text-right space-x-1">
                        <button data-id="${d.id}" class="btn-draft-edit bg-slate-100 text-slate-700 hover:bg-slate-200 px-2.5 py-1 rounded font-semibold text-xs">
                            EDITAR
                        </button>
                        <button data-id="${d.id}" class="btn-draft-export bg-slate-100 text-slate-700 hover:bg-slate-200 px-2 py-1 rounded font-semibold text-xs" title="Exportar JSON">
                            EXPORTAR
                        </button>
                        <button data-id="${d.id}" data-ver="${d.version_activa_numero || 1}" class="btn-draft-delete bg-red-50 text-red-700 hover:bg-red-100 px-2 py-1 rounded font-semibold text-xs">
                            BORRAR
                        </button>
                        <button data-id="${d.id}" data-ver="${d.version_activa_numero || 1}" class="btn-draft-deploy bg-emerald-700 text-white hover:bg-emerald-800 px-3 py-1 rounded font-semibold text-xs">
                            DESPLEGAR
                        </button>
                    </td>
                </tr>
            `;
        }).join("");

        container.innerHTML = `
            <div class="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-50 border-b border-slate-200 text-slate-600 text-xs font-semibold uppercase tracking-wider">
                            <th class="p-3">Nombre</th>
                            <th class="p-3">Zona</th>
                            <th class="p-3">Fecha</th>
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

        Drafts.bindEvents(container);
    },

    bindEvents: (container) => {
        // Editar Borrador
        container.querySelectorAll(".btn-draft-edit").forEach(btn => {
            btn.addEventListener("click", async () => {
                const id = parseInt(btn.dataset.id, 10);
                try {
                    const top = await API.query.getSliceTopology(id);
                    Wizard.open({
                        id: top.slice_id,
                        nombre: top.nombre,
                        zona_id: top.zona_id,
                        spec: top.spec,
                        version_activa_numero: top.numero_version || 1
                    }, () => Drafts.load(container.id));
                } catch (e) {
                    alert(`Error al cargar borrador: ${e.message}`);
                }
            });
        });

        // Exportar JSON
        container.querySelectorAll(".btn-draft-export").forEach(btn => {
            btn.addEventListener("click", async () => {
                const id = parseInt(btn.dataset.id, 10);
                try {
                    const top = await API.query.getSliceTopology(id);
                    const jsonStr = JSON.stringify(top.spec || { vms: [], links: [] }, null, 2);
                    const blob = new Blob([jsonStr], { type: "application/json" });
                    const url = URL.createObjectURL(blob);
                    const a = document.createElement("a");
                    a.href = url;
                    a.download = `${top.nombre || 'slice'}-spec.json`;
                    a.click();
                    URL.revokeObjectURL(url);
                } catch (e) {
                    alert(`Error al exportar JSON: ${e.message}`);
                }
            });
        });

        // Borrar Borrador
        container.querySelectorAll(".btn-draft-delete").forEach(btn => {
            btn.addEventListener("click", async () => {
                const id = parseInt(btn.dataset.id, 10);
                const ver = parseInt(btn.dataset.ver, 10) || 1;
                if (!confirm(`¿Estás seguro de eliminar permanentemente este borrador?`)) return;
                try {
                    await API.slices.deleteVersion(id, ver);
                    Drafts.load(container.id);
                } catch (e) {
                    alert(`Error al borrar borrador: ${e.message}`);
                }
            });
        });

        // Desplegar Borrador
        container.querySelectorAll(".btn-draft-deploy").forEach(btn => {
            btn.addEventListener("click", async () => {
                const id = parseInt(btn.dataset.id, 10);
                const ver = parseInt(btn.dataset.ver, 10) || 1;
                if (!confirm(`¿Confirmas el envío de este borrador a despliegue en la infraestructura?`)) return;
                try {
                    const res = await API.slices.deploy(id, ver);
                    if (res.state === "PENDING" || res.requires_approval) {
                        alert("Solicitud registrada con éxito. Está en revisión por el Operador de Plataforma.");
                    } else {
                        alert("Despliegue iniciado correctamente. Encolado en Temporal.");
                    }
                    Drafts.load(container.id);
                    window.dispatchEvent(new CustomEvent("slice:deployed"));
                } catch (e) {
                    alert(`Error al desplegar: ${e.message}`);
                }
            });
        });
    },

    importJsonFile: (file, onLoaded) => {
        const reader = new FileReader();
        reader.onload = (e) => {
            try {
                const spec = JSON.parse(e.target.result);
                if (!spec.vms || !Array.isArray(spec.vms)) {
                    throw new Error("El archivo JSON debe contener un arreglo 'vms'.");
                }
                // Normalizar links: si vienen con nodes, convertirlos a endpoints
                if (spec.links && Array.isArray(spec.links)) {
                    spec.links = spec.links.map(l => ({
                        name: l.name,
                        endpoints: l.endpoints || l.nodes || []
                    }));
                }
                Wizard.open({
                    nombre: file.name.replace(/\.[^/.]+$/, ""),
                    zona_id: 1,
                    spec
                }, onLoaded);
            } catch (err) {
                alert(`Archivo JSON inválido: ${err.message}`);
            }
        };
        reader.readAsText(file);
    }
};
