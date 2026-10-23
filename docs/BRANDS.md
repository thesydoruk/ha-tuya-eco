# Brand assets

Home Assistant shows integration icons from the central
[home-assistant/brands](https://github.com/home-assistant/brands) repository, not from the
integration itself. The assets live in `brand/` and are rendered by
`scripts/gen_brand.py` (Pillow, no external tools):

| file | size | use |
|---|---|---|
| `brand/icon.png` | 256×256 | integration icon |
| `brand/icon@2x.png` | 512×512 | hi-dpi icon |
| `brand/logo.png` | height 256 | wide logo with wordmark |
| `brand/logo@2x.png` | height 512 | hi-dpi logo |

## Submitting to home-assistant/brands

1. Fork <https://github.com/home-assistant/brands> and create
   `custom_integrations/tuya_eco/`.
2. Copy `icon.png`, `icon@2x.png`, `logo.png`, `logo@2x.png` from `brand/` into it
   (the folder name must equal the integration domain, `tuya_eco`).
3. Open a pull request titled "Add tuya_eco (custom integration)". The checks verify the
   sizes above and transparent backgrounds.
4. Once merged, remove `ignore: brands` from `.github/workflows/ci.yml` so the HACS
   validation covers the brand check again.

To change the artwork, edit `scripts/gen_brand.py` and run it; commit the regenerated PNGs.
