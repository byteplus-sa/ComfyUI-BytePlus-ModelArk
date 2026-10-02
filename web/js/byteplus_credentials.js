import { app } from "../../../scripts/app.js";
import { api } from "../../../scripts/api.js";

// Settings > BytePlus: default credentials, saved to ComfyUI's user/.env by the
// backend (nodes/credentials_routes.py). The keys never go through ComfyUI's
// settings store (so never into comfy.settings.json): the fields below talk to
// the backend directly, are cleared after saving, and the backend only ever
// answers with whether a key is set and its last four characters.

const ROUTE = "/byteplus/credentials";
const CATEGORY = ["BytePlus", "Default credentials"];

const SOURCE_TEXT = {
    environment: "from an environment variable",
    file: "from user/.env",
};

async function request(method, body) {
    const response = await api.fetchApi(ROUTE, {
        method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
    });
    let data = {};
    try {
        data = await response.json();
    } catch {
        // an empty or non-JSON body is reported through the status code below
    }
    if (!response.ok) {
        throw new Error(data.error || `Request failed (${response.status})`);
    }
    return data;
}

// The four rows render together and the status covers all of them, so they
// share one request (also when the dialog re-renders them).
let statusRequest = null;
function loadStatus() {
    if (!statusRequest) {
        statusRequest = request("GET");
        const reset = () => setTimeout(() => { statusRequest = null; }, 1000);
        statusRequest.then(reset, reset);
    }
    return statusRequest;
}

function element(tag, props = {}, style = {}) {
    const node = document.createElement(tag);
    Object.assign(node, props);
    Object.assign(node.style, style);
    return node;
}

const INPUT_STYLE = {
    flex: "1 1 12rem",
    minWidth: "0",
    padding: "0.35rem 0.5rem",
    borderRadius: "0.375rem",
    border: "1px solid var(--border-color, #4e4e4e)",
    background: "var(--comfy-input-bg, #222)",
    color: "var(--input-text, inherit)",
};

const BUTTON_STYLE = {
    padding: "0.35rem 0.8rem",
    borderRadius: "0.375rem",
    border: "1px solid var(--border-color, #4e4e4e)",
    background: "var(--comfy-menu-bg, #353535)",
    color: "var(--input-text, inherit)",
    cursor: "pointer",
    flex: "0 0 auto",
};

/**
 * A row of fields with Save / Remove buttons and a status line. `fields` are
 * {name, placeholder, type?} (password inputs by default); `select` an optional
 * {name, options} dropdown. `payload(values)` builds the POST body.
 */
function credentialEditor({ credential, fields, select, label }) {
    const root = element("div", {}, {
        display: "flex",
        flexDirection: "column",
        gap: "0.35rem",
        width: "30rem",
        maxWidth: "100%",
    });
    const row = element("div", {}, { display: "flex", gap: "0.4rem", flexWrap: "wrap", alignItems: "center" });
    const inputs = {};
    for (const field of fields) {
        const input = element("input", {
            type: "password",
            placeholder: field.placeholder,
            // "new-password" (not "off", which browsers ignore for passwords) and the
            // password managers' own opt-outs: an API key is not a login to remember.
            autocomplete: "new-password",
            spellcheck: false,
            name: `byteplus-${credential}-${field.name}`,
        }, INPUT_STYLE);
        for (const attribute of ["data-1p-ignore", "data-lpignore", "data-bwignore", "data-form-type"]) {
            input.setAttribute(attribute, attribute === "data-form-type" ? "other" : "true");
        }
        inputs[field.name] = input;
        row.append(input);
    }
    let regionSelect = null;
    // A region the user picked but has not saved yet survives re-renders.
    let regionTouched = false;
    if (select) {
        regionSelect = element("select", {}, { ...INPUT_STYLE, flex: "0 0 auto" });
        regionSelect.addEventListener("change", () => { regionTouched = true; });
        row.append(regionSelect);
    }
    const save = element("button", { type: "button", textContent: "Save" }, BUTTON_STYLE);
    const remove = element("button", { type: "button", textContent: "Remove" }, BUTTON_STYLE);
    row.append(save, remove);
    const status = element("div", {}, { fontSize: "0.8rem", opacity: "0.8", minHeight: "1.1rem" });
    root.append(row, status);

    const show = (text, isError = false) => {
        status.textContent = text;
        status.style.color = isError ? "var(--error-text, #ff6b6b)" : "";
    };

    let removable = false;
    const syncButtons = () => {
        save.disabled = false;
        remove.disabled = !removable;
    };

    const render = (data) => {
        const info = data.credentials?.[credential];
        if (regionSelect && data.regions && !regionSelect.options.length) {
            for (const region of data.regions) regionSelect.add(new Option(region, region));
        }
        if (regionSelect) {
            // BYTEPLUS_REGION in the environment overrides whatever is picked here.
            const locked = data.region_source === "environment";
            regionSelect.disabled = locked;
            regionSelect.title = locked
                ? "Set by the BYTEPLUS_REGION environment variable; change it there."
                : "";
            if (data.region && (locked || !regionTouched)) regionSelect.value = data.region;
        }
        if (!info?.configured) {
            show(`${label}: not set.`);
        } else {
            const parts = [`${label}: set (${info.hint})`, SOURCE_TEXT[info.source] ?? ""];
            let text = parts.filter(Boolean).join(", ");
            if (info.shadowed) text += ". An environment variable overrides the value in user/.env";
            show(text + ".");
        }
        // Remove deletes what user/.env holds, also when an environment variable hides it.
        removable = !!info?.in_file;
        if (!removable && info?.configured) {
            remove.title = "This value comes from an environment variable; remove it there.";
        } else if (removable && info.source === "environment") {
            remove.title = "Removes the value in user/.env; the environment variable stays in use.";
        } else {
            remove.title = "";
        }
        syncButtons();
    };

    const refresh = async () => {
        try {
            render(await loadStatus());
        } catch (error) {
            show(`Could not read the credential status: ${error.message}`, true);
        }
    };

    const send = async (body) => {
        save.disabled = remove.disabled = true;
        try {
            const data = await request("POST", { credential, ...body });
            for (const input of Object.values(inputs)) input.value = "";
            regionTouched = false;
            // The answer is the full status: update every row, not only this one.
            for (const editor of editors.values()) editor.render?.(data);
            if (data.message) status.textContent += ` ${data.message}`;
        } catch (error) {
            show(error.message, true);
            syncButtons();
        }
    };

    save.addEventListener("click", () => {
        const values = Object.fromEntries(Object.entries(inputs).map(([name, input]) => [name, input.value.trim()]));
        const missing = fields.find((field) => !values[field.name]);
        if (missing) {
            show(`Fill in ${missing.placeholder.toLowerCase()} first.`, true);
            return;
        }
        const body = fields.length === 1 && fields[0].name === "value" ? { value: values.value } : values;
        if (regionSelect) body.region = regionSelect.value;
        send(body);
    });
    remove.addEventListener("click", () => send({ clear: true }));

    refresh();
    // Re-read when the Settings dialog shows this row again (another tab may have changed the file).
    root.refresh = refresh;
    root.render = render;
    return root;
}

// ComfyUI re-runs a custom setting's render function whenever the dialog
// re-renders, so each editor is built once and handed out again.
const editors = new Map();
function editorFor(id, options) {
    let editor = editors.get(id);
    if (!editor) {
        editor = credentialEditor(options);
        editors.set(id, editor);
    } else {
        editor.refresh?.();
    }
    return editor;
}

function setting(id, name, tooltip, options) {
    return {
        id: `BytePlus.Credentials.${id}`,
        name,
        category: [...CATEGORY, name],
        tooltip,
        type: () => editorFor(id, options),
        // Never used: nothing is stored in ComfyUI's settings.
        defaultValue: "",
    };
}

app.registerExtension({
    name: "ComfyUI.BytePlus.Credentials",
    settings: [
        setting(
            "ModelArk",
            "ModelArk API key",
            "Used by the BytePlus nodes when no BytePlus API Client node is connected. Saved to user/.env as BYTEPLUS_API_KEY.",
            {
                credential: "modelark",
                label: "ModelArk API key",
                fields: [{ name: "value", placeholder: "ModelArk API key" }],
                select: { name: "region" },
            },
        ),
        setting(
            "SeedSpeech",
            "Seed Speech API key",
            "Separate from the ModelArk key. Used by the Seed Audio, TTS, ASR and Voice Clone nodes when no BytePlus Speech Client node is connected. Saved as BYTEPLUS_SEED_SPEECH_API_KEY.",
            {
                credential: "speech",
                label: "Seed Speech API key",
                fields: [{ name: "value", placeholder: "Seed Speech API key" }],
            },
        ),
        setting(
            "MediaKit",
            "AI MediaKit API key",
            "Separate from the ModelArk key. Used by the Video / Image Enhance nodes when no BytePlus MediaKit Client node is connected. Saved as BYTEPLUS_VOD_MEDIAKIT_API_KEY.",
            {
                credential: "mediakit",
                label: "AI MediaKit API key",
                fields: [{ name: "value", placeholder: "AI MediaKit API key" }],
            },
        ),
        setting(
            "AssetLibrary",
            "Asset library IAM AK/SK",
            "Only for the private asset library nodes and asset_N references. Use an IAM sub-user whose policy allows the asset library only. Saved as BYTEPLUS_ACCESS_KEY and BYTEPLUS_SECRET_KEY.",
            {
                credential: "iam",
                label: "IAM access key and secret key",
                fields: [
                    { name: "access_key", placeholder: "IAM access key (AK)" },
                    { name: "secret_key", placeholder: "IAM secret key (SK)" },
                ],
            },
        ),
    ],
});
