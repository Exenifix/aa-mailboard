/* Mail Board staff dashboard */
(function () {
    "use strict";

    const root = document.getElementById("mailboard-dashboard");
    if (!root) {
        return;
    }
    const ticketsUrl = root.dataset.ticketsUrl;
    const contactUrl = root.dataset.contactUrl;
    const csrfToken = root.dataset.csrf;
    const userId = Number(root.dataset.userId);
    const isAdmin = root.dataset.isAdmin === "1";
    const POLL_INTERVAL_MS = 10000;

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
    const ticketCategoryEl = document.getElementById("mb-ticket-category");
    const ticketAssigneeEl = document.getElementById("mb-ticket-assignee");
    const lockNoticeEl = document.getElementById("mb-lock-notice");
    const lockBtn = document.getElementById("mb-btn-lock");

    let currentTicketId = null;
    let currentTicket = null;
    let lastTicketJson = null;
    let suppressCategoryChange = false;
    let suppressAssigneeChange = false;

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

    function newDot(ticket) {
        return ticket.has_new_messages
            ? '<span class="mb-new-dot" title="New messages"></span>'
            : "";
    }

    function badges(ticket) {
        let html = "";
        html += ticket.is_closed
            ? '<span class="badge bg-secondary ms-1">Closed</span>'
            : '<span class="badge bg-success ms-1">Open</span>';
        if (ticket.is_locked) {
            html += '<span class="badge bg-dark ms-1">&#128274; Locked</span>';
        }
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
                    "<strong>" +
                    newDot(ticket) +
                    "#" +
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

    function refreshTicket() {
        // re-fetch the open ticket and re-render only when it changed
        fetch(ticketsUrl + "/" + currentTicketId, {
            headers: { Accept: "application/json" },
        })
            .then((response) => (response.ok ? response.json() : null))
            .then((ticket) => {
                if (ticket && JSON.stringify(ticket) !== lastTicketJson) {
                    renderTicket(ticket);
                }
            })
            .catch(() => {});
    }

    function renderTicket(ticket) {
        placeholderEl.classList.add("d-none");
        viewEl.classList.remove("d-none");
        showError("");
        currentTicket = ticket;
        lastTicketJson = JSON.stringify(ticket);
        titleEl.textContent = "#" + ticket.id + " " + ticket.title;
        badgesEl.innerHTML = badges(ticket);
        const isClosed = !!ticket.is_closed;
        // a lock only stops non-owners who are not admins
        const lockedAgainstMe =
            !!ticket.is_locked && ticket.assignee_id !== userId && !isAdmin;
        document.getElementById("mb-btn-send").classList.toggle("d-none", isClosed);
        document.getElementById("mb-btn-send-close").classList.toggle("d-none", isClosed);
        document.getElementById("mb-btn-close").classList.toggle("d-none", isClosed);
        document.getElementById("mb-btn-send").disabled = lockedAgainstMe;
        document.getElementById("mb-btn-send-close").disabled = lockedAgainstMe;
        document.getElementById("mb-btn-close").disabled = lockedAgainstMe;
        lockBtn.classList.toggle("d-none", isClosed);
        if (ticket.is_locked) {
            lockBtn.textContent = "Unlock";
            lockBtn.disabled = lockedAgainstMe;
        } else {
            lockBtn.textContent = "Lock";
            lockBtn.disabled = false;
        }
        lockNoticeEl.classList.toggle("d-none", !ticket.is_locked);
        if (ticket.is_locked) {
            lockNoticeEl.textContent = lockedAgainstMe
                ? "🔒 Locked by " +
                  (ticket.assignee || "?") +
                  " - only they or an admin can respond, change the category or close. You can still add notes."
                : "🔒 This ticket is locked to " +
                  (ticket.assignee_id === userId ? "you" : ticket.assignee || "?") +
                  ".";
        }
        suppressCategoryChange = true;
        ticketCategoryEl.value = ticket.category_id ? String(ticket.category_id) : "";
        suppressCategoryChange = false;
        ticketCategoryEl.disabled = isClosed || lockedAgainstMe;
        if (ticketAssigneeEl) {
            suppressAssigneeChange = true;
            ticketAssigneeEl.value = ticket.assignee_id ? String(ticket.assignee_id) : "";
            suppressAssigneeChange = false;
        }
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
                const isNote = message.type === "note";
                return (
                    '<div class="mb-message card mb-2 ' +
                    (isNote
                        ? "mb-message-note"
                        : isStaff
                          ? "mb-message-staff"
                          : "mb-message-client") +
                    '"><div class="card-body py-2">' +
                    '<div class="d-flex justify-content-between small text-muted mb-1"><span>' +
                    (message.character_id ? portrait(message.character_id) : "") +
                    esc(message.author) +
                    (isStaff ? " (staff)" : isNote ? " (staff note - not sent to client)" : "") +
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
        if (
            (action === "close" || action === "send_close") &&
            !window.confirm("Close ticket #" + currentTicketId + "?")
        ) {
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

    function changeCategory() {
        if (!currentTicketId || suppressCategoryChange) {
            return;
        }
        showError("");
        fetch(ticketsUrl + "/" + currentTicketId + "/category", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify({
                category_id: ticketCategoryEl.value || null,
            }),
        })
            .then((response) =>
                response.json().then((data) => ({ ok: response.ok, data: data }))
            )
            .then((result) => {
                if (!result.ok) {
                    showError(result.data.error || "Failed to change the category.");
                }
                openTicket(currentTicketId);
            })
            .catch(() => showError("Failed to change the category."));
    }

    function toggleLock() {
        if (!currentTicketId || !currentTicket) {
            return;
        }
        showError("");
        const endpoint = currentTicket.is_locked ? "/unlock" : "/lock";
        fetch(ticketsUrl + "/" + currentTicketId + endpoint, {
            method: "POST",
            headers: { "X-CSRFToken": csrfToken },
        })
            .then((response) =>
                response.json().then((data) => ({ ok: response.ok, data: data }))
            )
            .then((result) => {
                if (!result.ok) {
                    showError(result.data.error || "Request failed.");
                }
                openTicket(currentTicketId);
            })
            .catch(() => showError("Request failed."));
    }

    function changeAssignee() {
        if (!currentTicketId || suppressAssigneeChange) {
            return;
        }
        showError("");
        fetch(ticketsUrl + "/" + currentTicketId + "/assign", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify({
                user_id: ticketAssigneeEl.value || null,
            }),
        })
            .then((response) =>
                response.json().then((data) => ({ ok: response.ok, data: data }))
            )
            .then((result) => {
                if (!result.ok) {
                    showError(result.data.error || "Failed to change the assignee.");
                }
                openTicket(currentTicketId);
            })
            .catch(() => showError("Failed to change the assignee."));
    }

    function showContactError(message) {
        const el = document.getElementById("mb-contact-error");
        el.textContent = message;
        el.classList.toggle("d-none", !message);
    }

    function submitContact() {
        const characterEl = document.getElementById("mb-contact-character");
        const titleEl2 = document.getElementById("mb-contact-title");
        const categoryEl = document.getElementById("mb-contact-category");
        const contentEl = document.getElementById("mb-contact-content");
        showContactError("");
        fetch(contactUrl, {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
                "X-CSRFToken": csrfToken,
            },
            body: JSON.stringify({
                character: characterEl.value,
                title: titleEl2.value,
                category_id: categoryEl.value || null,
                content: contentEl.value,
            }),
        })
            .then((response) =>
                response.json().then((data) => ({ ok: response.ok, data: data }))
            )
            .then((result) => {
                if (!result.ok) {
                    showContactError(result.data.error || "Request failed.");
                    return;
                }
                characterEl.value = "";
                titleEl2.value = "";
                categoryEl.value = "";
                contentEl.value = "";
                const modalEl = document.getElementById("mb-contact-modal");
                if (window.bootstrap && window.bootstrap.Modal) {
                    window.bootstrap.Modal.getOrCreateInstance(modalEl).hide();
                }
                filterAll.checked = true;
                loadTickets();
                openTicket(result.data.id);
            })
            .catch(() => showContactError("Request failed."));
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
    document
        .getElementById("mb-btn-note")
        .addEventListener("click", () => submitAction("note"));
    document
        .getElementById("mb-contact-submit")
        .addEventListener("click", submitContact);
    lockBtn.addEventListener("click", toggleLock);
    ticketCategoryEl.addEventListener("change", changeCategory);
    if (ticketAssigneeEl) {
        ticketAssigneeEl.addEventListener("change", changeAssignee);
    }
    [filterCategory, filterStatus, filterAll].forEach((el) =>
        el.addEventListener("change", loadTickets)
    );

    // real-time updates: poll while the tab is visible
    setInterval(() => {
        if (document.hidden) {
            return;
        }
        loadTickets();
        if (currentTicketId) {
            refreshTicket();
        }
    }, POLL_INTERVAL_MS);

    loadTickets();
})();
