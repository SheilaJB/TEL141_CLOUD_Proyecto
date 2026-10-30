/**
 * topology.js — Motor Gráfico de Topologías de Red con Cytoscape.js
 * Orquestador Cloud TEL141 - Grupo 3
 */

export const TopologyViewer = {
    /**
     * Inicializa o actualiza un lienzo Cytoscape en el contenedor dado.
     */
    render: ({ container, spec, topologyType = "anillo", showNetworkDetails = true, onNodeSelect = null, isLive = false, servers = [] }) => {
        if (!container || !window.cytoscape) return null;

        const elements = [];
        const vms = spec?.vms || [];
        const links = spec?.links || [];

        // 1. Agrupación por Servidor Físico si está en vivo (VM Placement)
        const serverGroups = {};
        if (isLive && servers && servers.length > 0) {
            servers.forEach(srv => {
                const srvId = `server_${srv.id}`;
                serverGroups[srv.id] = srvId;
                elements.push({
                    data: {
                        id: srvId,
                        label: `Servidor ${srv.id} (${srv.ip_serv})`,
                        isServer: true
                    }
                });
            });
        }

        // 2. Nodos de VMs
        vms.forEach((vm, index) => {
            const parentId = (isLive && vm.servidor_id && serverGroups[vm.servidor_id]) 
                ? serverGroups[vm.servidor_id] 
                : undefined;

            let nodeLabel = vm.name;
            if (isLive && vm.ip) {
                nodeLabel += `\n${vm.ip}`;
            }

            elements.push({
                data: {
                    id: vm.name,
                    label: nodeLabel,
                    flavor: vm.flavor || vm.flavor_name || "micro-v1",
                    image: vm.image || vm.imagen_nombre || "Ubuntu",
                    status: vm.estado_nodo || (isLive ? "RUNNING" : "DRAFT"),
                    isPublic: vm.public || vm.public_access || false,
                    parent: parentId,
                    rawVm: vm
                }
            });
        });

        // 3. Nodos de Enlaces y Aristas
        if (topologyType === "bus") {
            // En topología bus, creamos un nodo visual "segmento_l2"
            const busId = "segmento_bus_l2";
            elements.push({
                data: {
                    id: busId,
                    label: "BUS L2 (Segmento Compartido)",
                    isBus: true
                }
            });
            vms.forEach(vm => {
                elements.push({
                    data: {
                        id: `edge_${vm.name}_${busId}`,
                        source: vm.name,
                        target: busId,
                        label: showNetworkDetails ? "eth0" : ""
                    }
                });
            });
        } else {
            // Enlaces punto a punto o multipunto
            links.forEach((link, idx) => {
                let endpoints = link.endpoints || link.nodes || [];
                if ((!endpoints || endpoints.length === 0) && link.puertos && Array.isArray(link.puertos)) {
                    endpoints = link.puertos.map(p => p.nodo_name).filter(Boolean);
                }

                if (endpoints.length === 2) {
                    const u = endpoints[0];
                    const v = endpoints[1];
                    let edgeLabel = "";
                    if (showNetworkDetails) {
                        if (link.cidr) edgeLabel += link.cidr;
                        if (link.vlan_ctag) edgeLabel += ` (VLAN ${link.vlan_ctag})`;
                    }

                    elements.push({
                        data: {
                            id: link.name || `link_${u}_${v}_${idx}`,
                            source: u,
                            target: v,
                            label: edgeLabel,
                            rawLink: link
                        }
                    });
                } else if (endpoints.length > 2) {
                    // Segmento compartido para enlaces multipunto (ej. LAN / Bus)
                    const hubId = `segment_${link.name || idx}`;
                    elements.push({
                        data: {
                            id: hubId,
                            label: link.name || "LAN",
                            isBus: true
                        }
                    });
                    endpoints.forEach((ep, epIdx) => {
                        elements.push({
                            data: {
                                id: `edge_${hubId}_${ep}_${epIdx}`,
                                source: ep,
                                target: hubId,
                                label: showNetworkDetails && link.cidr ? link.cidr : ""
                            }
                        });
                    });
                }
            });
        }

        // Estilos formales (alto contraste para proyector, sin emojis)
        const stylesheet = [
            {
                selector: "node[!isServer][!isBus]",
                style: {
                    "shape": "round-rectangle",
                    "width": "90px",
                    "height": "65px",
                    "background-color": isLive ? "#d1fae5" : "#f1f5f9",
                    "border-width": 2,
                    "border-color": isLive ? "#059669" : "#64748b",
                    "border-style": "solid",
                    "label": "data(label)",
                    "color": isLive ? "#065f46" : "#1e293b",
                    "font-size": "11px",
                    "font-weight": "bold",
                    "text-valign": "center",
                    "text-halign": "center",
                    "text-wrap": "wrap",
                    "text-max-width": "80px"
                }
            },
            {
                selector: "node[?isPublic]",
                style: {
                    "border-color": "#2563eb",
                    "border-width": 3
                }
            },
            {
                selector: "node[status = 'PENDING']",
                style: {
                    "background-color": "#fef3c7",
                    "border-color": "#d97706",
                    "color": "#92400e"
                }
            },
            {
                selector: "node[status = 'FAILED']",
                style: {
                    "background-color": "#fee2e2",
                    "border-color": "#dc2626",
                    "color": "#991b1b"
                }
            },
            {
                selector: "node[?isServer]",
                style: {
                    "shape": "round-rectangle",
                    "background-color": "#f8fafc",
                    "background-opacity": 0.8,
                    "border-width": 1.5,
                    "border-color": "#cbd5e1",
                    "border-style": "dashed",
                    "label": "data(label)",
                    "color": "#475569",
                    "font-size": "11px",
                    "font-weight": "bold",
                    "text-valign": "top",
                    "text-halign": "center",
                    "padding": "25px"
                }
            },
            {
                selector: "node[?isBus]",
                style: {
                    "shape": "rectangle",
                    "width": "220px",
                    "height": "16px",
                    "background-color": "#0284c7",
                    "border-width": 2,
                    "border-color": "#0369a1",
                    "label": "data(label)",
                    "color": "#ffffff",
                    "font-size": "9px",
                    "font-weight": "bold",
                    "text-valign": "center",
                    "text-halign": "center"
                }
            },
            {
                selector: "edge",
                style: {
                    "width": 2,
                    "line-color": "#94a3b8",
                    "target-arrow-shape": "none",
                    "curve-style": "bezier",
                    "label": "data(label)",
                    "font-size": "9px",
                    "color": "#475569",
                    "text-background-color": "#ffffff",
                    "text-background-opacity": 0.85,
                    "text-background-padding": "2px",
                    "text-rotation": "autorotate"
                }
            },
            {
                selector: "node:selected",
                style: {
                    "border-color": "#003a6c",
                    "border-width": 4,
                    "background-color": "#e0f2fe"
                }
            }
        ];

        // 4. Selección del Layout Determinista
        let layoutConfig = { name: "circle" };

        if (topologyType === "lineal") {
            // Posicionamiento horizontal fijo
            layoutConfig = {
                name: "grid",
                rows: 1,
                cols: Math.max(1, vms.length),
                padding: 30
            };
        } else if (topologyType === "arbol") {
            layoutConfig = {
                name: "breadthfirst",
                directed: false,
                padding: 30,
                spacingFactor: 1.25
            };
        } else if (topologyType === "bus") {
            layoutConfig = {
                name: "concentric",
                padding: 30
            };
        } else {
            // Anillo y Malla usan 'circle' determinista
            layoutConfig = {
                name: "circle",
                padding: 35,
                clockwise: true
            };
        }

        const cy = window.cytoscape({
            container,
            elements,
            style: stylesheet,
            layout: layoutConfig,
            userZoomingEnabled: true,
            userPanningEnabled: true,
            boxSelectionEnabled: false
        });

        if (onNodeSelect) {
            cy.on("tap", "node[!isServer][!isBus]", (evt) => {
                const node = evt.target;
                onNodeSelect(node.data("rawVm") || node.data());
            });
            cy.on("tap", (evt) => {
                if (evt.target === cy) {
                    onNodeSelect(null);
                }
            });
        }

        return cy;
    },

    exportPng: (cy) => {
        if (!cy) return null;
        return cy.png({ full: true, scale: 2, bg: "#ffffff" });
    }
};
