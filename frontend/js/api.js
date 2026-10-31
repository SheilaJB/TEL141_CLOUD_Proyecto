/**
 * api.js — Cliente Centralizado de Comunicación con el API Gateway (HTTPS 8443)
 * Orquestador Cloud TEL141 - Grupo 3
 */

const API_GATEWAY_URL = window.API_GATEWAY_URL || "https://localhost:8443";

export const Storage = {
    getToken: () => sessionStorage.getItem("jwt_token") || localStorage.getItem("jwt_token"),
    setToken: (token, remember = true) => {
        if (remember) localStorage.setItem("jwt_token", token);
        sessionStorage.setItem("jwt_token", token);
    },
    getUser: () => {
        const raw = sessionStorage.getItem("user_info") || localStorage.getItem("user_info");
        return raw ? JSON.parse(raw) : null;
    },
    setUser: (user, remember = true) => {
        const val = JSON.stringify(user);
        if (remember) localStorage.setItem("user_info", val);
        sessionStorage.setItem("user_info", val);
    },
    clear: () => {
        sessionStorage.clear();
        localStorage.clear();
    }
};

async function request(endpoint, options = {}) {
    const url = `${API_GATEWAY_URL}${endpoint}`;
    const token = Storage.getToken();

    const headers = {
        "Content-Type": "application/json",
        ...(options.headers || {})
    };

    if (token && !headers["Authorization"]) {
        headers["Authorization"] = `Bearer ${token}`;
    }

    try {
        const response = await fetch(url, {
            ...options,
            headers
        });

        if (response.status === 401) {
            Storage.clear();
            window.dispatchEvent(new CustomEvent("auth:expired"));
            throw new Error("Sesión expirada o no autorizada.");
        }

        if (response.status === 204) {
            return null;
        }

        const data = await response.json().catch(() => null);

        if (!response.ok) {
            const errorMsg = data?.detail || data?.message || `Error del servidor (${response.status})`;
            throw new Error(errorMsg);
        }

        return data;
    } catch (err) {
        if (err.name === "TypeError" && err.message.includes("Failed to fetch")) {
            throw new Error("No se pudo conectar con el API Gateway (puerto 8443). Verifique que el servicio esté activo y el certificado aceptado.");
        }
        throw err;
    }
}

export const API = {
    // ------------------------------------------------------------------------
    // Autenticación
    // ------------------------------------------------------------------------
    auth: {
        login: async (codigo, password) => {
            const data = await request("/auth/login", {
                method: "POST",
                body: JSON.stringify({ codigo, username: codigo, password, client: "web" })
            });
            return data;
        },
        logout: async () => {
            try {
                await request("/auth/logout", { method: "POST" });
            } finally {
                Storage.clear();
            }
        },
        verify: async () => {
            return await request("/auth/verify", { method: "GET" });
        }
    },

    // ------------------------------------------------------------------------
    // Consultas de Solo Lectura (Query Service vía /cruds)
    // ------------------------------------------------------------------------
    query: {
        getQuota: async () => request("/cruds/quota/me"),
        getSlices: async (estado = null) => {
            const query = estado ? `?estado=${encodeURIComponent(estado)}` : "";
            return request(`/cruds/slices${query}`);
        },
        getSliceDetail: async (sliceId) => request(`/cruds/slices/${sliceId}`),
        getSliceTopology: async (sliceId) => request(`/cruds/slices/${sliceId}/topology`),
        getRequests: async (estado = null) => {
            const query = estado ? `?estado=${encodeURIComponent(estado)}` : "";
            return request(`/cruds/requests${query}`);
        },
        getDeployment: async (deploymentId) => request(`/cruds/deployments/${deploymentId}`),
        getFlavors: async (activeOnly = true) => request(`/cruds/flavors?active_only=${activeOnly}`),
        getImages: async (activeOnly = true) => request(`/cruds/images?active_only=${activeOnly}`),
        getZones: async () => request("/cruds/zones"),
        getServers: async () => request("/cruds/servers"),
        getMetrics: async () => request("/cruds/metrics/overview")
    },

    // ------------------------------------------------------------------------
    // Acciones de Ciclo de Vida y Orquestación (Slice Manager vía /slices)
    // ------------------------------------------------------------------------
    slices: {
        create: async (body) => request("/slices", {
            method: "POST",
            body: JSON.stringify(body)
        }),
        createVersion: async (sliceId, fromVersion = null) => {
            const payload = fromVersion ? { from_version: fromVersion } : {};
            return request(`/slices/${sliceId}/versions`, {
                method: "POST",
                body: JSON.stringify(payload)
            });
        },
        updateVersionSpec: async (sliceId, versionNumber, spec) => request(`/slices/${sliceId}/versions/${versionNumber}`, {
            method: "PUT",
            body: JSON.stringify(spec)
        }),
        deleteVersion: async (sliceId, versionNumber) => request(`/slices/${sliceId}/versions/${versionNumber}`, {
            method: "DELETE"
        }),
        validateVersion: async (sliceId, versionNumber) => request(`/slices/${sliceId}/versions/${versionNumber}/validate`, {
            method: "POST"
        }),
        planVersion: async (sliceId, versionNumber) => request(`/slices/${sliceId}/versions/${versionNumber}/plan`, {
            method: "POST"
        }),
        deploy: async (sliceId, versionNumber) => request(`/slices/${sliceId}/versions/${versionNumber}/deploy`, {
            method: "POST"
        }),
        approveDeployment: async (deploymentId, approved, reason = null) => request(`/slices/deployments/${deploymentId}/approve`, {
            method: "POST",
            body: JSON.stringify({ approved, reason })
        }),
        destroy: async (sliceId) => request(`/slices/${sliceId}/destroy`, {
            method: "POST"
        })
    },

    // ------------------------------------------------------------------------
    // Operaciones Administrativas de Infraestructura e Identidad (Rol Admin)
    // ------------------------------------------------------------------------
    admin: {
        getQuotas: async () => request("/cruds/admin/quotas"),
        updateQuota: async (nivel_id, recurso, prometido) => request("/cruds/admin/quotas", {
            method: "PUT",
            body: JSON.stringify({ nivel_id, recurso, prometido })
        }),
        interveneSlice: async (sliceId, accion, motivo) => request(`/cruds/admin/slices/${sliceId}/intervene`, {
            method: "POST",
            body: JSON.stringify({ accion, motivo })
        }),
        toggleZone: async (zoneId) => request(`/cruds/admin/zones/${zoneId}/toggle`, {
            method: "PATCH"
        }),
        toggleFlavor: async (flavorId) => request(`/cruds/admin/flavors/${flavorId}/toggle`, {
            method: "PATCH"
        }),
        getUsers: async () => request("/auth/users"),
        createUser: async (user) => request("/auth/usuarios", {
            method: "POST",
            body: JSON.stringify(user)
        }),
        updateUser: async (userId, data) => request(`/auth/usuarios/${userId}`, {
            method: "PATCH",
            body: JSON.stringify(data)
        }),
        getAllSlices: async () => request("/cruds/slices"),
        getAuditLogs: async (params = {}) => {
            const qs = new URLSearchParams(params).toString();
            return request(`/cruds/admin/audit-logs${qs ? '?' + qs : ''}`);
        },
        getSecurityAnomalies: async () => request("/cruds/admin/security/anomalies"),
        purgeSliceSecurity: async (sliceId, motivo) => request("/cruds/admin/security/purge", {
            method: "POST",
            body: JSON.stringify({ slice_id: parseInt(sliceId, 10), motivo })
        })
    }
};
