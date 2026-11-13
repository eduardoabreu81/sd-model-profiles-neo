// Model Profiles for Forge Neo: the ⋯ menu, the name prompts, the history list, export / import and the "modified"
// mark of the Profile dropdown. The server keeps the profiles; this script reads the state it publishes in
// #model_profiles_state and sends commands through #model_profiles_cmd and the hidden buttons.

(() => {
    const ID = {
        select: "model_profiles_select",
        save: "model_profiles_save",
        more: "model_profiles_more",
        state: "model_profiles_state",
        cmd: "model_profiles_cmd",
        run: "model_profiles_run",
        restore: "model_profiles_restore",
        reapply: "model_profiles_reapply",
    };
    const DIRTY = "model-profiles-dirty";
    let menu = null;

    const app = () => (typeof gradioApp === "function" ? gradioApp() : document);
    const byId = (id) => app().querySelector(`#${id}`) || document.getElementById(id);
    const field = (id) => {
        const el = byId(id);
        return el && el.querySelector("textarea, input");
    };

    function readState() {
        const box = field(ID.state);
        try {
            return JSON.parse((box && box.value) || "{}");
        } catch {
            return {};
        }
    }

    // write a command for the server, then press the hidden button that reads it
    function send(payload, buttonId = ID.run) {
        const box = field(ID.cmd);
        const button = byId(buttonId);
        if (!box || !button) return;
        box.value = JSON.stringify({ ...payload, nonce: Date.now() });
        box.dispatchEvent(new Event("input", { bubbles: true }));
        setTimeout(() => button.click(), 0); // let Gradio take the new value first
    }

    // the value a Forge control shows: the number of a slider, the text of a dropdown
    function shown(elemId) {
        const root = byId(elemId);
        if (!root) return undefined;
        const input = root.querySelector("input[type=number]") || root.querySelector("input:not([type=range])");
        return input ? input.value : undefined;
    }

    function same(expected, actual) {
        if (actual === undefined) return true; // a control that is not on the page cannot differ
        if (typeof expected === "number") return Math.abs(parseFloat(actual) - expected) < 1e-6;
        return String(actual).trim() === String(expected).trim();
    }

    function changedControls(state) {
        const expect = (state && state.expect) || {};
        return Object.keys(expect).filter((id) => !same(expect[id], shown(id)));
    }

    function markDirty() {
        const state = readState();
        const select = byId(ID.select);
        if (!select) return;
        const dirty = !!state.selected && changedControls(state).length > 0;
        select.classList.toggle(DIRTY, dirty);
        const save = byId(ID.save);
        if (save) save.classList.toggle(DIRTY, dirty);
    }

    // after a profile is applied: a model extension's panel (H3, LTX, Idol...) may set its own sampler or steps when
    // the checkpoint changes; if the controls ended up different from the profile, apply its settings once more
    window.modelProfilesVerify = () => {
        setTimeout(() => {
            const state = readState();
            if (state.selected && changedControls(state).length) {
                const reapply = byId(ID.reapply);
                if (reapply) reapply.click();
            }
            markDirty();
        }, 700);
    };

    // after a menu command: hand an export over as a file
    window.modelProfilesAfterCommand = () => {
        const state = readState();
        if (!state.export) return;
        const blob = new Blob([JSON.stringify(state.export, null, 2) + "\n"], { type: "application/json" });
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = `model_profiles-${state.arch || "profiles"}.json`;
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
            URL.revokeObjectURL(a.href);
            a.remove();
        }, 1000);
    };

    function ask(message, value) {
        const answer = window.prompt(message, value ?? "");
        return answer === null ? null : answer.trim();
    }

    function saveNew(state) {
        const base = state.selected ? state.selected.name : "My profile";
        const suggested = state.selected && state.selected.editable ? `${base} 2` : `${base} (mine)`;
        const name = ask(`Save the current settings as a new profile for ${state.arch}:`, suggested);
        if (name) send({ op: "save_new", name });
    }

    function editTokens(sel) {
        const tokens = sel.tokens || {};
        const prompt = ask(
            `Words this profile adds to the prompt, separated by commas (LoRA tags such as <lora:name:1>).\nLeave empty for none.`,
            (tokens.prompt || []).join(", "),
        );
        if (prompt === null) return;
        const negative = ask("Words it adds to the negative prompt (optional):", (tokens.negative || []).join(", "));
        if (negative === null) return;
        send({ op: "tokens", data: { prompt, negative } });
    }

    function importFile() {
        const input = document.createElement("input");
        input.type = "file";
        input.accept = ".json,application/json";
        input.addEventListener("change", () => {
            const file = input.files && input.files[0];
            if (!file) return;
            file.text().then((text) => send({ op: "import", data: text }));
        });
        input.click();
    }

    function closeMenu() {
        if (menu) menu.remove();
        menu = null;
    }

    function openMenu(anchor, items) {
        closeMenu();
        menu = document.createElement("div");
        menu.className = "model-profiles-menu";
        menu.setAttribute("role", "menu");
        for (const item of items) {
            if (item.separator) {
                const hr = document.createElement("div");
                hr.className = "separator";
                menu.appendChild(hr);
                continue;
            }
            if (item.heading) {
                const h = document.createElement("div");
                h.className = "heading";
                h.textContent = item.heading;
                menu.appendChild(h);
                continue;
            }
            const b = document.createElement("button");
            b.type = "button";
            b.setAttribute("role", "menuitem");
            const label = document.createElement("span");
            label.textContent = item.label;
            b.appendChild(label);
            if (item.detail) {
                const detail = document.createElement("small");
                detail.textContent = item.detail;
                b.appendChild(detail);
            }
            if (item.danger) b.classList.add("danger");
            if (item.disabled) b.disabled = true;
            b.addEventListener("click", (e) => {
                e.stopPropagation();
                if (!item.keepOpen) closeMenu();
                item.run();
            });
            menu.appendChild(b);
        }
        document.body.appendChild(menu);
        const r = anchor.getBoundingClientRect();
        const left = Math.min(r.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8);
        menu.style.left = `${Math.max(8, left + window.scrollX)}px`;
        menu.style.top = `${r.bottom + 4 + window.scrollY}px`;
        const first = menu.querySelector("button:not([disabled])");
        if (first) first.focus();
    }

    function historyMenu(anchor, state) {
        const entries = state.history || [];
        const items = [{ heading: `History of ${state.arch}` }];
        if (!entries.length) items.push({ label: "Nothing recorded yet", disabled: true });
        for (const e of entries) {
            const what = e.kind === "profile" ? `profile “${e.name}”` : `preset (${e.name})`;
            items.push({
                label: `${e.at} · ${what}`,
                detail: `${e.reason}${e.summary ? " · " + e.summary : ""}`,
                run: () => send({ op: "restore", entry: e.id }, ID.restore),
            });
        }
        openMenu(anchor, items);
    }

    function mainMenu(anchor) {
        const state = readState();
        const sel = state.selected;
        const items = [
            { label: "Save as new profile…", run: () => saveNew(state) },
            sel && !sel.default && { label: `★ Set as default for ${state.arch}`, run: () => send({ op: "default" }) },
            sel && sel.default && { label: "Remove the ★ default", run: () => send({ op: "undefault" }) },
        ];
        if (sel && sel.editable) {
            const count = (sel.tokens.prompt || []).length + (sel.tokens.negative || []).length;
            items.push(
                { separator: true },
                { label: "Prompt tokens / LoRA…", detail: count ? `${count} set` : "none", run: () => editTokens(sel) },
                sel.has_checkpoint && {
                    label: sel.linked ? "✓ Applied when its checkpoint is chosen" : "Apply when its checkpoint is chosen",
                    run: () => send({ op: sel.linked ? "unlink" : "link" }),
                },
                {
                    label: "Rename…",
                    run: () => {
                        const name = ask("New name:", sel.name);
                        if (name) send({ op: "rename", name });
                    },
                },
                {
                    label: "Delete…",
                    danger: true,
                    run: () => {
                        if (window.confirm(`Delete the profile "${sel.name}"? It can be restored from History.`)) {
                            send({ op: "delete" });
                        }
                    },
                },
            );
        }
        items.push(
            { separator: true },
            { label: "History…", detail: `${(state.history || []).length} entries`, keepOpen: true, run: () => historyMenu(anchor, state) },
            { label: `Export my ${state.arch} profiles`, run: () => send({ op: "export" }) },
            { label: "Import profiles…", run: importFile },
        );
        openMenu(anchor, items.filter(Boolean));
    }

    document.addEventListener("click", (e) => {
        const target = e.target instanceof Element ? e.target : null;
        if (!target) return;
        const more = target.closest(`#${ID.more}`);
        if (menu && !menu.contains(target) && !more) closeMenu();
        if (target.closest(`#${ID.save}`)) {
            const state = readState();
            if (state.selected && state.selected.editable) send({ op: "save" });
            else saveNew(state);
        } else if (more) {
            if (menu) closeMenu();
            else mainMenu(more);
        }
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeMenu();
    });

    const start = () => setInterval(markDirty, 800);
    if (typeof onUiLoaded === "function") onUiLoaded(start);
    else window.addEventListener("load", start);
})();
