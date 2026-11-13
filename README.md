# 🎚️ Model Profiles for Forge Neo

<div align="center">

[![Forge Neo](https://img.shields.io/badge/Forge-Neo-blue)](https://github.com/Haoming02/sd-webui-forge-classic/tree/neo)
[![Gradio](https://img.shields.io/badge/Gradio-4.40.0-orange)](https://gradio.app/)
[![Version](https://img.shields.io/badge/Version-0.1.0-brightgreen)](https://github.com/eduardoabreu81/sd-model-profiles-neo)
[![License](https://img.shields.io/badge/License-AGPL--3.0-green.svg)](LICENSE)

> **Extension for [Stable Diffusion WebUI Forge - Neo](https://github.com/Haoming02/sd-webui-forge-classic/tree/neo)**

</div>

Keep **several setups for the same UI Preset** - Qwen-Image 2.1 *Base* and *Turbo*, SDXL and Pony, a fast draft and a final pass - and switch between them in one click. A profile holds what Forge's preset holds: **checkpoint, VAE / Text Encoder, Diffusion in Low Bits, sampler, schedule type, steps, CFG, Shift / Distilled CFG, Hires. steps and CFG, width and height**, for txt2img and img2img.

> [!Important]
> Model Profiles builds on Forge Neo's own **UI Preset** settings (Settings > presets) and selects model files through Forge's own functions. Nothing of Forge Neo is changed, and generation is never touched.

---

## ✨ Features

- **One dropdown** next to **UI Preset**, listing the profiles of the preset on screen: yours first, then the suggestions
- **💾 Save** updates the profile you are using; on a suggestion it makes a copy among yours
- **⋯** - save as a new profile, set or remove the **★ default**, rename, delete
- **★ Default per preset** - leaving a preset puts its starred profile back, so every visit starts from it while the others stay one click away. No star, no change: Forge behaves as before
- **Suggestions** - **Forge default** (the factory values of every preset, including the ones added by model extensions) and any `model_profiles.json` shipped by an extension
- **"modified" mark** when the controls no longer match the profile
- **Generation parameters** - `Profile: Turbo 8 steps` (or `(modified)`); *Send to txt2img* and *PNG Info* bring the profile back to the dropdown
- **Safe with model extensions** - presets added at runtime (Qwen-Image 2.1, MiniMax-H3, LTX, MOSS-Image, ...) get their profiles too, and when an extension's panel changes the sampler or steps on a checkpoint switch, the profile's values are applied again
- **Nothing lost** - the first time a preset is written, its current Settings values are kept as the profile *My Forge preset* if you had changed them

## 📦 Installation

1. In Forge Neo, open **Extensions > Install from URL**
2. Paste `https://github.com/eduardoabreu81/sd-model-profiles-neo` and click **Install**
3. Restart Forge Neo

## 🛠️ How to Use

1. Pick a **UI Preset** as usual
2. Set the checkpoint, sampler, steps and the rest the way this model wants them
3. Click **💾** and name the profile (*Turbo 8 steps*)
4. Set up the other variant and save it too (*Base 20 steps*)
5. Switch with the **Profile** dropdown; mark the one you use most with **⋯ > ★ Set as default**

Profiles are kept in `model_profiles.json` in Forge's data folder (next to `styles.csv`), so updating the extension never touches them. Profiles of a preset whose extension is turned off stay there and come back with it.

### Settings

**Settings > presets > Model Profiles**

| Option | Default |
| --- | --- |
| Leaving a UI Preset puts its ★ default profile back into it | on |
| Write the profile name to the generation parameters | on |

## 🧩 Suggestions from Extensions

Any extension can suggest settings for its model: put a `model_profiles.json` in the extension's folder. Without Model Profiles installed, the file is simply ignored.

```json
{
  "version": 1,
  "profiles": [
    {"preset": "qwen21", "name": "Base", "values": {"sampler": "Euler", "scheduler": "Simple", "steps": 20, "cfg": 2.5}},
    {"preset": "qwen21", "name": "Turbo", "values": {"scheduler": "Viggle Turbo", "steps": 8, "cfg": 1}},
    {"preset": "h3i", "name": "LCM 6 steps", "values": {"sampler": "LCM", "scheduler": "Simple", "steps": 6, "cfg": 1,
                                                        "img2img": {"steps": 8}}}
  ]
}
```

- `preset` is the UI Preset name (`xl`, `flux`, `qwen21`, or one an extension adds)
- top-level settings apply to txt2img and img2img; `txt2img` / `img2img` objects override them
- settings: `sampler`, `scheduler`, `steps`, `cfg`, `shift` (or `distilled_cfg`), `width`, `height`, and for txt2img `hr_steps`, `hr_cfg`, `hr_shift`
- model files: `checkpoint`, `modules` (list of file names), `dtype`
- anything left out keeps its current value, so a suggestion can change only the sampler and steps
- a sampler, schedule or file that is not installed is skipped with a warning; the rest is applied

## 🙏 Credits

- [Forge Neo](https://github.com/Haoming02/sd-webui-forge-classic/tree/neo) by Haoming02, whose per-preset settings this builds on
- The idea came from [Haoming02/sd-webui-forge-classic#1636](https://github.com/Haoming02/sd-webui-forge-classic/issues/1636)
- [sd-aspect-ratio-plus](https://github.com/anxety-solo/sd-aspect-ratio-plus) for the "one click, no clutter" spirit
