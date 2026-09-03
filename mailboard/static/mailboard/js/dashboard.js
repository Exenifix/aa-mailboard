/* Mail Board staff dashboard */
(function () {
    "use strict";

    const root = document.getElementById("mailboard-dashboard");
    if (!root) {
        return;
    }
    const ticketsUrl = root.dataset.ticketsUrl;
    const csrfToken = root.dataset.csrf;

    const listEl = document.getElementById("mb-ticket-list");
    const viewEl = document.getElementById("mb-ticket-view");
    const placeholderEl = document.getElementById("mb-ticket-placeholder");
    const titleEl = document.getElementById("mb-ticket-title");
    const badgesEl = document.getElementById("mb-ticket-badges");
    const metaEl = document.getElementById("mb-ticket-meta");
    const messagesEl = document.getElementById("mb-messages");
    const replyEl = document.getElementById("mb-reply-content");
    const errorEl = document.getElementById("mb-error");

    const filterCategory = document.getElementById("mb-filter-category");
    const filterStatus = document.getElementById("mb-filter-status");
    const filterAll = document.getElementById("mb-filter-all");

    let currentTicketId = null;

    function esc(text) {
        const div = document.createElement("div");
        div.textContent = text == null ? "" : String(text);
        return div.innerHTML;
    }

    function showError(message) {
        errorEl.textContent = message;
        errorEl.classList.toggle("d-none", !message);
    }

    function portrait(characterId) {
        return (
            '<img class="mb-portrait rounded me-2" alt="" src="https://images.evetech.net/characters/' +
            encodeURIComponent(characterId) +
            '/portrait?size=32">'
        );
    }

    function badges(ticket) {
        let html = "";
        html += ticket.is_closed
            ? '<span class="badge bg-secondary ms-1">Closed</span>'
            : '<span class="badge bg-success ms-1">Open</span>';
        if (ticket.assignee) {
            html +=
                '<span class="badge bg-info ms-1">' + esc(ticket.assignee) + "</span>";
        }
        html +=
            '<span class="badge bg-light text-dark border ms-1">' +
            esc(ticket.category || "Other") +
            "</span>";
        return html;
    }

    function loadTickets() {
        const params = new URLSearchParams();
        if (filterCategory.value) {
            params.set("category", filterCategory.value);
        }
        if (filterStatus.value) {
            params.set("status", filterStatus.value);
        }
        if (filterAll.checked) {
            params.set("all", "1");
        }
        fetch(ticketsUrl + "?" + params.toString(), {
            headers: { Accept: "application/json" },
        })
            .then((response) => response.json())
            .then((data) => renderTicketList(data.tickets || []))
            .catch(() => {
                listEl.innerHTML =
                    '<div class="list-group-item text-danger">Failed to load tickets.</div>';
            });
    }

    function renderTicketList(tickets) {
        if (!tickets.length) {
            listEl.innerHTML =
                '<div class="list-group-item text-muted">No tickets found.</div>';
            return;
        }
        listEl.innerHTML = tickets
            .map(
                (ticket) =>
                    '<a href="#" class="list-group-item list-group-item-action' +
                    (ticket.id === currentTicketId ? " active" : "") +
                    '" data-ticket-id="' +
                    ticket.id +
                    '">' +
                    '<div class="d-flex w-100 justify-content-between">' +
                    "<strong>#" +
                    ticket.id +
                    " " +
                    esc(ticket.title) +
                    "</strong><span>" +
                    badges(ticket) +
                    "</span></div>" +
                    '<small class="text-muted">' +
                    esc(ticket.creator_character.name) +
                    " · " +
                    new Date(ticket.created_at).toLocaleString() +
                    "</small></a>"
            )
            .join("");
        listEl.querySelectorAll("[data-ticket-id]").forEach((el) => {
            el.addEventListener("click", (event) => {
                event.preventDefault();
                openTicket(Number(el.dataset.ticketId));
            });
        });
    }

    function openTicket(ticketId) {
        fetch(ticketsUrl + "/" + ticketId, {
            headers: { Accept: "application/json" },
        })
            .then((response) => response.json())
            .then((ticket) => {
                currentTicketId = ticketId;
                renderTicket(ticket);
                loadTickets();
            })
            .catch(() => showError("Failed to load the ticket."));
    }

    function renderTicket(ticket) {
        placeholderEl.classList.add("d-none");
        viewEl.classList.remove("d-none");
        showError("");
        titleEl.textContent = "#" + ticket.id + " " + ticket.title;
        badgesEl.innerHTML = badges(ticket);
        const isClosed = !!ticket.is_closed;
        replyEl.classList.toggle("d-none", isClosed);
        document.getElementById("mb-btn-send").classList.toggle("d-none", isClosed);
        document.getElementById("mb-btn-send-close").classList.toggle("d-none", isClosed);
        document.getElementById("mb-btn-close").classList.toggle("d-none", isClosed);
        metaEl.innerHTML =
            "Created by " +
            portrait(ticket.creator_character.id) +
            esc(ticket.creator_character.name) +
            " on " +
            new Date(ticket.created_at).toLocaleString() +
            " via " +
            esc(ticket.source);
        messagesEl.innerHTML = (ticket.messages || [])
            .map((message) => {
                const isStaff = message.type === "staff";
                return (
                    '<div class="mb-message card mb-2 ' +
                    (isStaff ? "mb-message-staff" : "mb-message-client") +
                    '"><div class="card-body py-2">' +
                    '<div class="d-flex justify-content-between small text-muted mb-1"><span>' +
                    (message.character_id ? portrait(message.character_id) : "") +
                    esc(message.author) +
                    (isStaff ? " (staff)" : "") +
                    "</span><span>" +
                    new Date(message.timestamp).toLocaleString() +
                    "</span></div>" +
                    '<div class="mb-message-content">' +
                    esc(message.content) +
                    "</div></div></div>"
                );
            })
            .join("");
        messagesEl.scrollTop = messagesEl.scrollHeight;
    }

    function submitAction(action) {
        if (!currentTicketId) {
            return;
        }
        showError("");
        fetch(ticketsUrl + "/" + currentTicketId + "/reply", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify({
                action: action,
                content: replyEl.value,
            }),
        })
            .then((response) =>
                response.json().then((data) => ({ ok: response.ok, data: data }))
            )
            .then((result) => {
                if (!result.ok) {
                    showError(result.data.error || "Request failed.");
                    return;
                }
                replyEl.value = "";
                openTicket(currentTicketId);
            })
            .catch(() => showError("Request failed."));
    }

    document
        .getElementById("mb-btn-send")
        .addEventListener("click", () => submitAction("send"));
    document
        .getElementById("mb-btn-send-close")
        .addEventListener("click", () => submitAction("send_close"));
    document
        .getElementById("mb-btn-close")
        .addEventListener("click", () => submitAction("close"));
    [filterCategory, filterStatus, filterAll].forEach((el) =>
        el.addEventListener("change", loadTickets)
    );

    loadTickets();
})();
