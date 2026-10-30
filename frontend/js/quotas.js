/**
 * quotas.js — Renderizado y Monitoreo de Cuotas en Tiempo Real
 * Orquestador Cloud TEL141 - Grupo 3
 */

import { API } from "./api.js";

let currentQuota = null;

export const Quotas = {
    get: () => currentQuota,

    load: async (containerId) => {
        const container = document.getElementById(containerId);
        if (!container) return;

        try {
            const data = await API.query.getQuota();
            currentQuota = data;
            Quotas.render(container, data);
            return data;
        } catch (err) {
            container.innerHTML = `
                <div class="col-span-3 p-4 bg-red-50 border border-red-200 text-red-700 rounded-lg text-sm">
                    No se pudieron cargar las cuotas: ${err.message}
                </div>
            `;
            return null;
        }
    },

    render: (container, data) => {
        if (!data || !data.cuotas || data.cuotas.length === 0) {
            container.innerHTML = `
                <div class="col-span-3 p-4 bg-slate-100 border border-slate-200 text-slate-600 rounded-lg text-sm">
                    No hay cuotas asignadas para este perfil.
                </div>
            `;
            return;
        }

        const map = {};
        data.cuotas.forEach(q => { map[q.recurso] = q; });

        const vcpu = map["vcpu"] || { prometido: 0, consumido_activos: 0, reservado_pendientes: 0, disponible: 0 };
        const ram = map["ram_mb"] || { prometido: 0, consumido_activos: 0, reservado_pendientes: 0, disponible: 0 };
        const disk = map["disk_mb"] || { prometido: 0, consumido_activos: 0, reservado_pendientes: 0, disponible: 0 };

        const vcpuUsed = vcpu.consumido_activos + vcpu.reservado_pendientes;
        const vcpuPct = vcpu.prometido > 0 ? Math.min(100, Math.round((vcpuUsed / vcpu.prometido) * 100)) : 0;

        const ramUsedMb = ram.consumido_activos + ram.reservado_pendientes;
        const ramUsedGib = (ramUsedMb / 1024).toFixed(1);
        const ramPromGib = (ram.prometido / 1024).toFixed(1);
        const ramPct = ram.prometido > 0 ? Math.min(100, Math.round((ramUsedMb / ram.prometido) * 100)) : 0;

        const diskUsedMb = disk.consumido_activos + disk.reservado_pendientes;
        const diskUsedGib = (diskUsedMb / 1024).toFixed(1);
        const diskPromGib = (disk.prometido / 1024).toFixed(1);
        const diskPct = disk.prometido > 0 ? Math.min(100, Math.round((diskUsedMb / disk.prometido) * 100)) : 0;

        const getBarColor = (pct) => {
            if (pct >= 90) return "bg-red-600";
            if (pct >= 70) return "bg-amber-500";
            return "bg-emerald-600";
        };

        container.innerHTML = `
            <!-- vCPU -->
            <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-xs font-semibold uppercase tracking-wider text-slate-500">vCPU Cores</span>
                    <span class="text-xs font-bold px-2 py-0.5 rounded-full ${vcpuPct >= 90 ? 'bg-red-100 text-red-800' : 'bg-slate-100 text-slate-700'}">${vcpuPct}%</span>
                </div>
                <div class="flex items-baseline space-x-2">
                    <span class="text-2xl font-bold text-slate-900">${vcpuUsed}</span>
                    <span class="text-sm font-medium text-slate-500">/ ${vcpu.prometido} Cores</span>
                </div>
                <div class="w-full bg-slate-100 h-2 rounded-full mt-3 overflow-hidden">
                    <div class="${getBarColor(vcpuPct)} h-full rounded-full transition-all duration-500" style="width: ${vcpuPct}%"></div>
                </div>
                <div class="mt-2 text-xs text-slate-400">
                    Activos: ${vcpu.consumido_activos} | En reserva: ${vcpu.reservado_pendientes}
                </div>
            </div>

            <!-- RAM -->
            <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-xs font-semibold uppercase tracking-wider text-slate-500">Memoria RAM</span>
                    <span class="text-xs font-bold px-2 py-0.5 rounded-full ${ramPct >= 90 ? 'bg-red-100 text-red-800' : 'bg-slate-100 text-slate-700'}">${ramPct}%</span>
                </div>
                <div class="flex items-baseline space-x-2">
                    <span class="text-2xl font-bold text-slate-900">${ramUsedGib}</span>
                    <span class="text-sm font-medium text-slate-500">/ ${ramPromGib} GiB</span>
                </div>
                <div class="w-full bg-slate-100 h-2 rounded-full mt-3 overflow-hidden">
                    <div class="${getBarColor(ramPct)} h-full rounded-full transition-all duration-500" style="width: ${ramPct}%"></div>
                </div>
                <div class="mt-2 text-xs text-slate-400" title="${ramUsedMb} MB / ${ram.prometido} MB">
                    Disponible: ${((ram.disponible || 0) / 1024).toFixed(1)} GiB (${ram.disponible || 0} MB)
                </div>
            </div>

            <!-- DISCO -->
            <div class="bg-white p-5 rounded-xl border border-slate-200 shadow-sm">
                <div class="flex items-center justify-between mb-2">
                    <span class="text-xs font-semibold uppercase tracking-wider text-slate-500">Almacenamiento</span>
                    <span class="text-xs font-bold px-2 py-0.5 rounded-full ${diskPct >= 90 ? 'bg-red-100 text-red-800' : 'bg-slate-100 text-slate-700'}">${diskPct}%</span>
                </div>
                <div class="flex items-baseline space-x-2">
                    <span class="text-2xl font-bold text-slate-900">${diskUsedGib}</span>
                    <span class="text-sm font-medium text-slate-500">/ ${diskPromGib} GiB</span>
                </div>
                <div class="w-full bg-slate-100 h-2 rounded-full mt-3 overflow-hidden">
                    <div class="${getBarColor(diskPct)} h-full rounded-full transition-all duration-500" style="width: ${diskPct}%"></div>
                </div>
                <div class="mt-2 text-xs text-slate-400" title="${diskUsedMb} MB / ${disk.prometido} MB">
                    Disponible: ${((disk.disponible || 0) / 1024).toFixed(1)} GiB (${disk.disponible || 0} MB)
                </div>
            </div>
        `;
    }
};
