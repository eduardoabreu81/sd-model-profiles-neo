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

    // a checkpoint may show with or without its " [hash]"
    const plain = (text) => String(text).trim().replace(/\s\[[0-9a-f]{8,12}\]$/i, "");

    function same(expected, actual) {
        if (actual === undefined) return true; // a control that is not on the page cannot differ
        if (typeof expected === "number") return Math.abs(parseFloat(actual) - expected) < 1e-6;
        return plain(actual) === plain(expected);
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

    // After a profile is applied: a model extension's panel (H3, LTX, Idol...) may set its own sampler or steps when
    // the checkpoint changes, and it can take seconds (Idol reads the file first). For a few seconds, whenever the
    // controls drift from the profile, apply its settings again; stop at once if the user touches anything.
    const WATCH_MS = 9000;
    const MAX_REAPPLY = 3;
    let watch = null;

    function stopWatch() {
        if (!watch) return;
        clearInterval(watch.timer);
        document.removeEventListener("input", watch.onUser, true);
        document.removeEventListener("mousedown", watch.onUser, true);
        document.removeEventListener("keydown", watch.onUser, true);
        watch = null;
    }

    window.modelProfilesVerify = () => {
        stopWatch();
        const started = Date.now();
        const w = { reapplied: 0, settleUntil: started + 500 };
        w.onUser = (e) => {
            if (!e.isTrusted || Date.now() < w.settleUntil) return;
            const t = e.target instanceof Element ? e.target : null;
            if (t && t.closest(".model-profiles-menu, #model_profiles_select")) return;
            stopWatch();
        };
        w.timer = setInterval(() => {
            const state = readState();
            if (Date.now() - started > WATCH_MS || !state.selected) {
                stopWatch();
                markDirty();
                return;
            }
            if (Date.now() >= w.settleUntil && changedControls(state).length && w.reapplied < MAX_REAPPLY) {
                const reapply = byId(ID.reapply);
                if (reapply) {
                    reapply.click();
                    w.reapplied += 1;
                    w.settleUntil = Date.now() + 600; // let the reapplied values arrive before checking again
                }
            }
        }, 300);
        document.addEventListener("input", w.onUser, true);
        document.addEventListener("mousedown", w.onUser, true);
        document.addEventListener("keydown", w.onUser, true);
        watch = w;
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

    const SCOPES = [
        { id: "checkpoint", label: "This checkpoint", hint: (s) => s.checkpoint || "no checkpoint selected" },
        { id: "preset", label: "This preset", hint: (s) => `any ${s.arch} checkpoint` },
        { id: "global", label: "Every preset", hint: () => "steps, CFG, sampler and tokens only" },
    ];
    const scopeName = (id) => (SCOPES.find((x) => x.id === id) || { label: id }).label;

    // a small panel under the anchor: a name (when saving) and where the profile applies
    function scopePanel(anchor, { title, name, scope, submitLabel, onSubmit }) {
        closeMenu();
        const state = readState();
        menu = document.createElement("form");
        menu.className = "model-profiles-menu model-profiles-panel";
        const heading = document.createElement("div");
        heading.className = "heading";
        heading.textContent = title;
        menu.appendChild(heading);
        let input = null;
        if (name !== undefined) {
            input = document.createElement("input");
            input.type = "text";
            input.value = name;
            input.placeholder = "Profile name";
            input.setAttribute("aria-label", "Profile name");
            menu.appendChild(input);
        }
        const group = document.createElement("div");
        group.className = "scopes";
        group.setAttribute("role", "radiogroup");
        group.setAttribute("aria-label", "Applies to");
        for (const sc of SCOPES) {
            const label = document.createElement("label");
            const radio = document.createElement("input");
            radio.type = "radio";
            radio.name = "model-profiles-scope";
            radio.value = sc.id;
            radio.checked = sc.id === scope;
            radio.disabled = sc.id === "checkpoint" && !state.checkpoint;
            const text = document.createElement("span");
            text.textContent = sc.label;
            const hint = document.createElement("small");
            hint.textContent = sc.hint(state);
            label.append(radio, text, hint);
            group.appendChild(label);
        }
        menu.appendChild(group);
        const row = document.createElement("div");
        row.className = "actions";
        const ok = document.createElement("button");
        ok.type = "submit";
        ok.className = "primary";
        ok.textContent = submitLabel;
        const cancel = document.createElement("button");
        cancel.type = "button";
        cancel.textContent = "Cancel";
        cancel.addEventListener("click", closeMenu);
        row.append(ok, cancel);
        menu.appendChild(row);
        menu.addEventListener("submit", (e) => {
            e.preventDefault();
            const chosen = menu.querySelector("input[type=radio]:checked");
            const value = input ? input.value.trim() : null;
            if (input && !value) {
                input.focus();
                return;
            }
            closeMenu();
            onSubmit(value, chosen ? chosen.value : "preset");
        });
        document.body.appendChild(menu);
        const r = anchor.getBoundingClientRect();
        const left = Math.min(r.left, window.innerWidth - menu.offsetWidth - 8);
        menu.style.left = `${Math.max(8, left + window.scrollX)}px`;
        menu.style.top = `${r.bottom + 4 + window.scrollY}px`;
        (input || menu.querySelector("input[type=radio]:checked") || ok).focus();
        if (input) input.select();
    }

    function saveNew(state, anchor) {
        const sel = state.selected;
        const base = sel ? sel.name : "My profile";
        const suggested = sel && sel.editable ? `${base} 2` : `${base} (mine)`;
        const scope = sel && sel.editable && sel.scope ? sel.scope : "preset";
        scopePanel(anchor, {
            title: "Save as a new profile",
            name: suggested,
            scope: scope === "checkpoint" && !state.checkpoint ? "preset" : scope,
            submitLabel: "Save",
            onSubmit: (name, chosen) => send({ op: "save_new", name, data: { scope: chosen } }),
        });
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
            { label: "Save as new profile…", run: () => saveNew(state, anchor) },
            sel && !sel.default && { label: `★ Set as default for ${state.arch}`, run: () => send({ op: "default" }) },
            sel && sel.default && { label: "Remove the ★ default", run: () => send({ op: "undefault" }) },
        ];
        if (sel && sel.editable) {
            const count = (sel.tokens.prompt || []).length + (sel.tokens.negative || []).length;
            items.push(
                { separator: true },
                { label: "Prompt tokens / LoRA…", detail: count ? `${count} set` : "none", run: () => editTokens(sel) },
                {
                    label: "Applies to…",
                    detail: scopeName(sel.scope),
                    keepOpen: true,
                    run: () =>
                        scopePanel(anchor, {
                            title: `Where "${sel.name}" applies`,
                            scope: sel.scope,
                            submitLabel: "Change",
                            onSubmit: (_, chosen) => {
                                if (chosen !== sel.scope) send({ op: "scope", data: { scope: chosen } });
                            },
                        }),
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
        const saveButton = target.closest(`#${ID.save}`);
        if (saveButton) {
            const state = readState();
            if (state.selected && state.selected.editable) send({ op: "save" });
            else saveNew(state, saveButton);
        } else if (more) {
            if (menu) closeMenu();
            else mainMenu(more);
        }
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeMenu();
    });

    // Profile on a row of its own under Forge's model row (UI Preset, Checkpoint, VAE / Text Encoder, Low Bits),
    // so it never pushes those around whatever the window width
    function placeRow() {
        const select = byId(ID.select);
        const save = byId(ID.save);
        const more = byId(ID.more);
        const bar = byId("quicksettings");
        if (!select || !save || !more || !bar) return false;
        if (select.closest(".model-profiles-row")) return true;
        const row = document.createElement("div");
        row.className = "model-profiles-row";
        bar.parentNode.insertBefore(row, bar.nextSibling);
        row.append(select, save, more);
        return true;
    }

    const start = () => {
        let tries = 0;
        const wait = setInterval(() => {
            if (placeRow() || ++tries > 50) clearInterval(wait);
        }, 200);
        setInterval(markDirty, 800);
    };
    if (typeof onUiLoaded === "function") onUiLoaded(start);
    else window.addEventListener("load", start);
})();
