# Expoplanet Discovery with TESS

This project is a starter Python workspace for exploring TESS data from MAST and inspecting light curves for potential exoplanet transits.

## What it does

- Queries TESS light curves from the Mikulski Archive for Space Telescopes (MAST)
- Downloads a light curve for a target such as a TIC ID or star name
- Plots the raw and flattened light curve so you can inspect potential dips

## Setup

1. Create and activate a virtual environment if you want an isolated install.
2. Install the dependencies:

   ```bash
   python3 -m pip install -r requirements.txt
   ```

3. Run the example:

   ```bash
   python3 main.py --target "TIC 261136679" --sector 1
   ```

## Notes

- MAST access requires internet access and may take a moment to download data.
- The script saves a PNG plot in the project folder by default.
- For a more advanced workflow, you can later add transit search algorithms such as Box Least Squares.
