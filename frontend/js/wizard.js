/**
 * wizard.js — Diseñador de Borradores y Generador de Topologías
 * Orquestador Cloud TEL141 - Grupo 3
 */

import { API } from "./api.js";
import { Quotas } from "./quotas.js";
import { TopologyViewer } from "./topology.js";

let cyInstance = null;
let flavorsList = [];
let imagesList = [];
let zonesList = [];
let debounceTimer = null;

let state = {
    sliceName: "mi-borrador-slice",
    zoneId: null,
    topologyType: "anillo",
    numVms: 4,
    vms: [],
    links: [],
    jsonError: null
};

// Generador matemático de topologías en JavaScript (acorde a las reglas de NetworkX)
export function generateTopologyGraph(type, n) {
    const vms = [];
    const links = [];

    // Validar topes prácticos
    const clampedN = Math.max(type === "anillo" ? 3 : 2, Math.min(type === "malla" ? 8 : 12, n));

    for (let i = 1; i <= clampedN; i++) {
        vms.push({
            name: `vm${i}`,
            flavor: "micro-v1",
            image: "Ubuntu",
            public: i === 1 // Solo la primera con IP pública por defecto
        });
    }

    if (type === "lineal") {
        for (let i = 1; i < clampedN; i++) {
            links.push({
                name: `link_vm${i}_vm${i+1}`,
                endpoints: [`vm${i}`, `vm${i+1}`]
            });
        }
    } else if (type === "anillo") {
        for (let i = 1; i <= clampedN; i++) {
            const next = i === clampedN ? 1 : i + 1;
            links.push({
                name: `link_vm${i}_vm${next}`,
                endpoints: [`vm${i}`, `vm${next}`]
            });
        }
    } else if (type === "malla") {
        for (let i = 1; i <= clampedN; i++) {
            for (let j = i + 1; j <= clampedN; j++) {
                links.push({
                    name: `link_vm${i}_vm${j}`,
                    endpoints: [`vm${i}`, `vm${j}`]
                });
            }
        }
    } else if (type === "arbol") {
        // Árbol binario por niveles: el padre del nodo i (0-indexed) es floor((i-1)/2)
        for (let i = 1; i < clampedN; i++) {
            const parentIdx = Math.floor((i - 1) / 2) + 1;
            links.push({
                name: `link_vm${parentIdx}_vm${i+1}`,
                endpoints: [`vm${parentIdx}`, `vm${i+1}`]
            });
        }
    } else if (type === "bus") {
        // En topología bus (segmento L2 compartido), un link con todos los endpoints de las VMs
        const busEndpoints = [];
        for (let i = 1; i <= clampedN; i++) {
            busEndpoints.push(`vm${i}`);
        }
        links.push({
            name: "lan_bus_segmento",
            endpoints: busEndpoints
        });
    }

    return { vms, links };
}

export const Wizard = {
    open: async (existingDraft = null, onSaved = null) => {
        const modal = document.getElementById("wizard-modal");
        if (!modal) return;

        // Cargar catálogos reales desde el Query Service
        try {
            [flavorsList, imagesList, zonesList] = await Promise.all([
                API.query.getFlavors(true),
                API.query.getImages(true),
                API.query.getZones()
            ]);
        } catch (e) {
            alert(`Error al cargar catálogos: ${e.message}`);
            return;
        }

        if (existingDraft) {
            const spec = existingDraft.spec || {};
            state.sliceName = existingDraft.nombre || "borrador";
            state.zoneId = existingDraft.zona_id || (zonesList[0]?.id || 1);
            state.existingSliceId = existingDraft.id;
            state.versionNumber = existingDraft.version_activa_numero || 1;

            if (spec.vms && spec.vms.length > 0) {
                state.vms = spec.vms.map(v => ({ ...v }));
                state.links = (spec.links || []).map(l => ({
                    name: l.name,
                    endpoints: l.endpoints || l.nodes || []
                }));
                state.numVms = state.vms.length;
            } else {
                // Si el borrador existente viene sin nodos (ej. borrador inicial vacío), inicializar plantilla
                state.topologyType = "anillo";
                state.numVms = 4;
                const generated = generateTopologyGraph(state.topologyType, state.numVms);
                state.vms = generated.vms;
                state.links = generated.links;
            }
        } else {
            state.sliceName = `slice-${Date.now().toString().slice(-4)}`;
            state.zoneId = zonesList[0]?.id || 1;
            state.topologyType = "anillo";
            state.numVms = 4;
            const generated = generateTopologyGraph(state.topologyType, state.numVms);
            state.vms = generated.vms;
            state.links = generated.links;
            state.existingSliceId = null;
            state.versionNumber = 1;
        }

        Wizard.render();
        modal.classList.remove("hidden");
        Wizard.updateGraphPreview();

        // Guardar callback
        Wizard._onSaved = onSaved;
    },

    close: () => {
        const modal = document.getElementById("wizard-modal");
        if (modal) modal.classList.add("hidden");
        if (cyInstance) {
            cyInstance.destroy();
            cyInstance = null;
        }
    },

    getState: () => state,

    calculateRequiredResources: () => {
        let vcpuSum = 0;
        let ramMbSum = 0;
        let diskMbSum = 0;

        const flavorMap = {};
        flavorsList.forEach(f => { flavorMap[f.name] = f; });

        state.vms.forEach(vm => {
            const flv = flavorMap[vm.flavor] || { vcpu: 1, vram_mb: 256, vdisk_mb: 1000 };
            vcpuSum += flv.vcpu;
            ramMbSum += flv.vram_mb;
            diskMbSum += flv.vdisk_mb;
        });

        return { vcpu: vcpuSum, ramMb: ramMbSum, diskMb: diskMbSum };
    },

    checkQuotaCompliance: () => {
        const currentQuota = Quotas.get();
        if (!currentQuota || !currentQuota.cuotas) return { ok: true, reason: "" };

        const req = Wizard.calculateRequiredResources();
        const map = {};
        currentQuota.cuotas.forEach(q => { map[q.recurso] = q; });

        const vcpu = map["vcpu"];
        const ram = map["ram_mb"];
        const disk = map["disk_mb"];

        if (vcpu && (vcpu.disponible < req.vcpu)) {
            return { ok: false, reason: `vCPU insuficiente: requiere ${req.vcpu} Cores, disponible ${vcpu.disponible}` };
        }
        if (ram && (ram.disponible < req.ramMb)) {
            return { ok: false, reason: `RAM insuficiente: requiere ${(req.ramMb/1024).toFixed(1)} GiB, disponible ${(ram.disponible/1024).toFixed(1)} GiB` };
        }
        if (disk && (disk.disponible < req.diskMb)) {
            return { ok: false, reason: `Disco insuficiente: requiere ${(req.diskMb/1024).toFixed(1)} GiB, disponible ${(disk.disponible/1024).toFixed(1)} GiB` };
        }

        return { ok: true, reason: "" };
    },

    updateGraphPreview: () => {
        if (debounceTimer) clearTimeout(debounceTimer);
        debounceTimer = setTimeout(() => {
            const container = document.getElementById("wizard-cytoscape-container");
            if (!container) return;

            const spec = { vms: state.vms, links: state.links };
            cyInstance = TopologyViewer.render({
                container,
                spec,
                topologyType: state.topologyType,
                showNetworkDetails: false,
                isLive: false
            });
        }, 150);
    },

    render: () => {
        const req = Wizard.calculateRequiredResources();
        const quotaCheck = Wizard.checkQuotaCompliance();

        // 1. Zonas disponibles
        const zoneOptions = zonesList.map(z => 
            `<option value="${z.id}" ${state.zoneId == z.id ? 'selected' : ''}>${z.nombre} (Cluster: ${z.cluster_id === 1 ? 'Linux' : 'OpenStack'})</option>`
        ).join("");

        // 2. Opciones de Flavor e Imagen
        const flavorOptions = (selected) => flavorsList.map(f => 
            `<option value="${f.name}" ${selected === f.name ? 'selected' : ''}>${f.name} (${f.vcpu}vCPU, ${f.vram_mb}MB)</option>`
        ).join("");

        const imageOptions = (selected) => imagesList.map(img => 
            `<option value="${img.nombre}" ${selected === img.nombre ? 'selected' : ''}>${img.nombre}</option>`
        ).join("");

        // 3. Filas de VMs
        const vmRows = state.vms.map((vm, idx) => `
            <div class="bg-slate-50 p-3 rounded-lg border border-slate-200 text-xs mb-2">
                <div class="flex items-center justify-between mb-2">
                    <span class="font-bold text-slate-800">${vm.name}</span>
                    <label class="flex items-center space-x-1 cursor-pointer">
                        <input type="checkbox" data-idx="${idx}" class="vm-public-chk rounded text-blue-900 focus:ring-0" ${vm.public ? 'checked' : ''}>
                        <span class="text-slate-600 font-medium">IP Pública</span>
                    </label>
                </div>
                <div class="grid grid-cols-2 gap-2">
                    <div>
                        <label class="block text-slate-500 mb-1">Flavor</label>
                        <select data-idx="${idx}" class="vm-flavor-sel w-full bg-white border border-slate-300 rounded p-1 text-slate-800">
                            ${flavorOptions(vm.flavor)}
                        </select>
                    </div>
                    <div>
                        <label class="block text-slate-500 mb-1">Imagen</label>
                        <select data-idx="${idx}" class="vm-image-sel w-full bg-white border border-slate-300 rounded p-1 text-slate-800">
                            ${imageOptions(vm.image)}
                        </select>
                    </div>
                </div>
            </div>
        `).join("");

        const content = document.getElementById("wizard-form-container");
        if (!content) return;

        content.innerHTML = `
            <!-- Nombre y Zona -->
            <div class="grid grid-cols-2 gap-3 mb-4">
                <div>
                    <label class="block text-xs font-semibold text-slate-600 mb-1">Nombre del Slice</label>
                    <input type="text" id="wizard-name-input" value="${state.sliceName}" class="w-full bg-white border border-slate-300 rounded-lg p-2 text-xs text-slate-800 focus:outline-none focus:border-blue-900">
                </div>
                <div>
                    <label class="block text-xs font-semibold text-slate-600 mb-1">Zona de Disponibilidad</label>
                    <select id="wizard-zone-select" class="w-full bg-white border border-slate-300 rounded-lg p-2 text-xs text-slate-800 focus:outline-none focus:border-blue-900">
                        ${zoneOptions}
                    </select>
                </div>
            </div>

            <!-- Previsualización de Impacto en Cuota -->
            <div class="p-3 mb-4 rounded-lg text-xs ${quotaCheck.ok ? 'bg-slate-100 text-slate-700' : 'bg-red-50 text-red-700 border border-red-200'}">
                <div class="flex items-center justify-between font-semibold mb-1">
                    <span>Impacto en Cuota de este Borrador:</span>
                    <span class="${quotaCheck.ok ? 'text-emerald-700' : 'text-red-700 font-bold'}">${quotaCheck.ok ? 'DENTRO DE CUOTA' : 'EXCEDE CUOTA'}</span>
                </div>
                <div class="flex space-x-4 text-slate-600">
                    <span>vCPU: <b>+${req.vcpu} Cores</b></span>
                    <span>RAM: <b>+${(req.ramMb/1024).toFixed(1)} GiB</b></span>
                    <span>Disco: <b>+${(req.diskMb/1024).toFixed(1)} GiB</b></span>
                </div>
                ${!quotaCheck.ok ? `<div class="mt-1 font-semibold text-red-600">${quotaCheck.reason}</div>` : ''}
            </div>

            <!-- Selector de Topología Base -->
            <div class="mb-4">
                <label class="block text-xs font-semibold text-slate-600 mb-1">Topología Base (NetworkX)</label>
                <div class="grid grid-cols-5 gap-1 mb-2">
                    ${['lineal', 'anillo', 'malla', 'arbol', 'bus'].map(t => `
                        <button type="button" data-topo="${t}" class="wizard-topo-btn py-1.5 px-1 rounded text-xs font-semibold uppercase border ${state.topologyType === t ? 'bg-blue-900 text-white border-blue-900' : 'bg-white text-slate-700 border-slate-300 hover:bg-slate-50'}">
                            ${t}
                        </button>
                    `).join('')}
                </div>
                <div class="flex items-center justify-between mt-2">
                    <span class="text-xs text-slate-500">Cantidad de VMs:</span>
                    <input type="number" id="wizard-num-vms" min="${state.topologyType === 'anillo' ? 3 : 2}" max="${state.topologyType === 'malla' ? 8 : 12}" value="${state.numVms}" class="w-20 bg-white border border-slate-300 rounded p-1 text-xs text-center font-bold text-slate-800">
                </div>
            </div>

            <!-- Fila Aplicar a Todas -->
            <div class="p-2.5 bg-slate-100 rounded-lg mb-3 flex items-center justify-between text-xs">
                <span class="font-semibold text-slate-700">Fijar todas las VMs con:</span>
                <div class="flex space-x-2">
                    <select id="apply-all-flavor" class="p-1 bg-white border border-slate-300 rounded text-xs">
                        ${flavorOptions(flavorsList[0]?.name)}
                    </select>
                    <select id="apply-all-image" class="p-1 bg-white border border-slate-300 rounded text-xs">
                        ${imageOptions(imagesList[0]?.nombre)}
                    </select>
                    <button type="button" id="btn-apply-all" class="bg-slate-700 text-white px-2 py-1 rounded font-semibold hover:bg-slate-800">
                        APLICAR
                    </button>
                </div>
            </div>

            <!-- Lista de VMs -->
            <div class="max-h-64 overflow-y-auto pr-1 mb-4">
                ${vmRows}
            </div>
        `;

        Wizard.bindEvents();
    },

    bindEvents: () => {
        // Nombre y Zona
        const nameInput = document.getElementById("wizard-name-input");
        if (nameInput) {
            nameInput.addEventListener("input", (e) => { state.sliceName = e.target.value.trim(); });
        }
        const zoneSelect = document.getElementById("wizard-zone-select");
        if (zoneSelect) {
            zoneSelect.addEventListener("change", (e) => { state.zoneId = parseInt(e.target.value, 10); });
        }

        // Botones de Topología
        document.querySelectorAll(".wizard-topo-btn").forEach(btn => {
            btn.addEventListener("click", () => {
                const topo = btn.dataset.topo;
                state.topologyType = topo;
                const gen = generateTopologyGraph(topo, state.numVms);
                state.vms = gen.vms;
                state.links = gen.links;
                Wizard.render();
                Wizard.updateGraphPreview();
            });
        });

        // Input numérico N
        const numInput = document.getElementById("wizard-num-vms");
        if (numInput) {
            numInput.addEventListener("change", (e) => {
                const n = parseInt(e.target.value, 10) || 3;
                state.numVms = n;
                const gen = generateTopologyGraph(state.topologyType, n);
                state.vms = gen.vms;
                state.links = gen.links;
                Wizard.render();
                Wizard.updateGraphPreview();
            });
        }

        // Desplegables de cada VM
        document.querySelectorAll(".vm-flavor-sel").forEach(sel => {
            sel.addEventListener("change", (e) => {
                const idx = parseInt(e.target.dataset.idx, 10);
                state.vms[idx].flavor = e.target.value;
                Wizard.render();
            });
        });

        document.querySelectorAll(".vm-image-sel").forEach(sel => {
            sel.addEventListener("change", (e) => {
                const idx = parseInt(e.target.dataset.idx, 10);
                state.vms[idx].image = e.target.value;
            });
        });

        document.querySelectorAll(".vm-public-chk").forEach(chk => {
            chk.addEventListener("change", (e) => {
                const idx = parseInt(e.target.dataset.idx, 10);
                state.vms[idx].public = e.target.checked;
                Wizard.updateGraphPreview();
            });
        });

        // Aplicar a todas
        const btnApplyAll = document.getElementById("btn-apply-all");
        if (btnApplyAll) {
            btnApplyAll.addEventListener("click", () => {
                const flv = document.getElementById("apply-all-flavor").value;
                const img = document.getElementById("apply-all-image").value;
                state.vms.forEach(vm => {
                    vm.flavor = flv;
                    vm.image = img;
                });
                Wizard.render();
            });
        }

        // Botón Guardar Borrador
        const btnSave = document.getElementById("wizard-save-btn");
        if (btnSave) {
            btnSave.onclick = async () => {
                await Wizard.saveDraft();
            };
        }

        // Botón Desplegar desde Wizard
        const btnDeploy = document.getElementById("wizard-deploy-btn");
        if (btnDeploy) {
            const quota = Wizard.checkQuotaCompliance();
            btnDeploy.disabled = !quota.ok;
            btnDeploy.onclick = async () => {
                await Wizard.deployDirect();
            };
        }
    },

    buildCleanSpec: () => {
        return {
            schema_version: 1,
            vms: state.vms.map(v => ({
                name: v.name.trim(),
                flavor: v.flavor.trim(),
                image: v.image.trim(),
                public: Boolean(v.public)
            })),
            links: state.links.map(l => ({
                name: l.name.trim(),
                endpoints: (l.endpoints || l.nodes || []).map(ep => ep.trim())
            }))
        };
    },

    saveDraft: async () => {
        const spec = Wizard.buildCleanSpec();

        try {
            if (state.existingSliceId) {
                // Actualizar versión existente
                await API.slices.updateVersionSpec(state.existingSliceId, state.versionNumber, spec);
                alert("Borrador actualizado con éxito.");
            } else {
                // Crear nuevo slice borrador
                await API.slices.create({
                    name: state.sliceName || "nuevo-slice",
                    zona_id: state.zoneId || 1,
                    spec
                });
                alert("Borrador creado exitosamente.");
            }

            Wizard.close();
            if (Wizard._onSaved) Wizard._onSaved();
        } catch (e) {
            alert(`Error al guardar borrador: ${e.message}`);
        }
    },

    deployDirect: async () => {
        const quota = Wizard.checkQuotaCompliance();
        if (!quota.ok) {
            alert(`No se puede desplegar: ${quota.reason}`);
            return;
        }

        if (!confirm("¿Confirmas el envío de este borrador para despliegue en la nube?")) return;

        try {
            const spec = Wizard.buildCleanSpec();
            let sliceId = state.existingSliceId;
            let verNum = state.versionNumber;

            if (!sliceId) {
                const created = await API.slices.create({
                    name: state.sliceName,
                    zona_id: state.zoneId,
                    spec
                });
                sliceId = created.slice_id;
                verNum = created.numero_version;
            } else {
                await API.slices.updateVersionSpec(sliceId, verNum, spec);
            }

            // Desplegar versión
            const res = await API.slices.deploy(sliceId, verNum);
            
            if (res.state === "PENDING" || res.requires_approval) {
                alert("Solicitud registrada con éxito. Está en revisión por el Operador de Plataforma.");
            } else {
                alert("Despliegue iniciado correctamente. El aprovisionamiento en la nube ha comenzado.");
            }

            Wizard.close();
            if (Wizard._onSaved) Wizard._onSaved();
        } catch (e) {
            alert(`Error en despliegue: ${e.message}`);
        }
    }
};
