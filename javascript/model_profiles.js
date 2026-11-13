// Model Profiles for Forge Neo: the ⋯ menu, the save prompts and the "modified" mark of the Profile dropdown.
// The server keeps the profiles; this script only reads the state it publishes in #model_profiles_state and sends
// commands through #model_profiles_cmd + #model_profiles_run.

(() => {
    const ID = {
        select: "model_profiles_select",
        save: "model_profiles_save",
        more: "model_profiles_more",
        state: "model_profiles_state",
        cmd: "model_profiles_cmd",
        run: "model_profiles_run",
        reapply: "model_profiles_reapply",
    };
    const DIRTY = "model-profiles-dirty";
    let menu = null;

    const app = () => (typeof gradioApp === "function" ? gradioApp() : document);
    const byId = (id) => app().querySelector(`#${id}`) || document.getElementById(id);

    function readState() {
        const el = byId(ID.state);
        const box = el && el.querySelector("textarea, input");
        try {
            return JSON.parse((box && box.value) || "{}");
        } catch {
            return {};
        }
    }

    function send(op, name) {
        const el = byId(ID.cmd);
        const box = el && el.querySelector("textarea, input");
        const run = byId(ID.run);
        if (!box || !run) return;
        box.value = JSON.stringify({ op, name: name ?? null, nonce: Date.now() });
        box.dispatchEvent(new Event("input", { bubbles: true }));
        // let Gradio take the new value before the click reads it
        setTimeout(() => run.click(), 0);
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

    function closeMenu() {
        if (menu) menu.remove();
        menu = null;
    }

    function ask(message, value) {
        const name = window.prompt(message, value || "");
        return name && name.trim() ? name.trim() : null;
    }

    function saveNew(state) {
        const base = state.selected ? state.selected.name : "My profile";
        const suggested = state.selected && state.selected.editable ? `${base} 2` : `${base} (mine)`;
        const name = ask(`Save the current settings as a new profile for ${state.arch}:`, suggested);
        if (name) send("save_new", name);
    }

    function openMenu(button) {
        closeMenu();
        const state = readState();
        const sel = state.selected;
        const items = [
            { label: "Save as new profile…", run: () => saveNew(state) },
            sel && !sel.default && { label: `★ Set as default for ${state.arch}`, run: () => send("default") },
            sel && sel.default && { label: "Remove the ★ default", run: () => send("undefault") },
            sel && sel.editable && {
                label: "Rename…",
                run: () => {
                    const name = ask("New name:", sel.name);
                    if (name) send("rename", name);
                },
            },
            sel && sel.editable && {
                label: "Delete…",
                danger: true,
                run: () => {
                    if (window.confirm(`Delete the profile "${sel.name}"? Suggestions are not affected.`)) send("delete");
                },
            },
        ].filter(Boolean);
        if (!sel) items.push({ label: "Select a profile for more options", disabled: true });

        menu = document.createElement("div");
        menu.className = "model-profiles-menu";
        menu.setAttribute("role", "menu");
        for (const item of items) {
            const b = document.createElement("button");
            b.type = "button";
            b.textContent = item.label;
            b.setAttribute("role", "menuitem");
            if (item.danger) b.classList.add("danger");
            if (item.disabled) b.disabled = true;
            b.addEventListener("click", () => {
                closeMenu();
                item.run();
            });
            menu.appendChild(b);
        }
        document.body.appendChild(menu);
        const r = button.getBoundingClientRect();
        const left = Math.min(r.right - menu.offsetWidth, window.innerWidth - menu.offsetWidth - 8);
        menu.style.left = `${Math.max(8, left + window.scrollX)}px`;
        menu.style.top = `${r.bottom + 4 + window.scrollY}px`;
        const first = menu.querySelector("button:not([disabled])");
        if (first) first.focus();
    }

    document.addEventListener("click", (e) => {
        const target = e.target instanceof Element ? e.target : null;
        if (!target) return;
        if (menu && !menu.contains(target) && !target.closest(`#${ID.more}`)) closeMenu();
        if (target.closest(`#${ID.save}`)) {
            const state = readState();
            if (state.selected && state.selected.editable) send("save");
            else saveNew(state);
        } else if (target.closest(`#${ID.more}`)) {
            if (menu) closeMenu();
            else openMenu(target.closest(`#${ID.more}`));
        }
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") closeMenu();
    });

    const start = () => setInterval(markDirty, 800);
    if (typeof onUiLoaded === "function") onUiLoaded(start);
    else window.addEventListener("load", start);
})();
