# Data sources, licences and attributions

Generated from `catalog/sources.yaml` (catalogue version 0.2.0, working commit `b71ad77`). Do not edit by hand: change the catalogue.

Every source keeps its own terms. The application shows the licence and the citation of each source next to
the figures that come from it. The project's own code and materials are under Apache 2.0 (see `LICENSE`).
This is a reading of the official pages, not legal advice.

| Source | Provider | Resolution | Since | Access | Account |
| --- | --- | --- | --- | --- | --- |
| [Landsat 8/9 Collection 2 Level-2](#landsat-c2-l2) | USGS and NASA | 30 m (surface temperature: ~100 m) | 2013 | api | none |
| [NASA POWER](#nasa-power) | NASA LaRC | ~50 km | 1981 | api | none |
| [SMAP L3 soil moisture 9 km](#smap-l3) | NASA NSIDC DAAC | 9 km | 2015 | package | earthdata |
| [Plan Hidrológico de la Demarcación Hidrográfica del Júcar 2022-2027](#chj-phj-2022-2027) | Confederación Hidrográfica del Júcar |  | 2023 | manual | none |
| [GPM IMERG Final Precipitation L3, monthly](#imerg-monthly) | NASA GES DISC | 0.1° (about 11 km) | 1998 | package | earthdata |
| [NASA Earth Exchange Global Daily Downscaled Projections (NEX-GDDP-CMIP6)](#nex-gddp-cmip6) | NASA NCCS | 0.25° (about 28 km) | 1995 | package | none |
| [Global Human Settlement Layer population grid](#ghsl) | European Commission Joint Research Centre | 100 m (teselas R5_C18 y R5_C19) y 1 km (global) |  | package | none |
| [Copernicus EMS observed flood extent, activation EMSR773](#copernicus-ems-emsr773) | Copernicus Emergency Management Service |  |  | package | none |
| [PATRICOVA flood hazard](#patricova) | Generalitat Valenciana |  |  | package | none |
| [INE census section boundaries](#ine) | Instituto Nacional de Estadística |  |  | package | none |
| [INE population by census section and official municipal figures](#ine-population) | Instituto Nacional de Estadística | census section | 2021 | package | none |
| [SEDAC SSP population projections, 1/8 degree](#sedac-ssp) | NASA SEDAC (CIESIN) | 1/8 degree | 2000 | package | none |
| [JRC Global Surface Water, water occurrence](#jrc-gsw) | European Commission Joint Research Centre and Google | 30 m | 1984 | package | none |
| [Real Decreto 35/2023, de 24 de enero, Plan Hidrológico del Júcar (BOE-A-2023-3511)](#boe-rd-35-2023) | Agencia Estatal Boletín Oficial del Estado |  | 2023 | manual | none |
| [Global Human Settlement Layer built-up surface grid](#ghsl-built-s) | European Commission Joint Research Centre | 100 m | 1975 | package | none |
| [Sistema Nacional de Cartografía de Zonas Inundables, flood hazard areas](#snczi) | Ministerio para la Transición Ecológica y el Reto Demográfico (MITECO) | vector, scale 1:25,000 |  | package | none |
| [Sentinel-1 Radiometrically Terrain Corrected (RTC)](#sentinel-1-rtc) | European Space Agency (Copernicus Sentinel-1); RTC processing by Catalyst, served by Microsoft Planetary Computer | 10 m (read at 20 m) | 2014 | api | none |
| [GHRSST MUR SST v4.1, daily sea surface temperature](#mur-sst) | NASA JPL (PO.DAAC); public Zarr copy on AWS Open Data | 0.01 degree (~1 km) | 2002 | api | none |
| [Planeamiento urbanístico de la Comunitat Valenciana (clasificación del suelo)](#icv-planeamiento) | Institut Cartogràfic Valencià, Generalitat Valenciana |  | 2026 | package | none |
| [Red de control piezométrico de la CHJ (F71, Puntos de control piezométrico)](#chj-f71) | Confederación Hidrográfica del Júcar | point (monitoring well) | 1959 | package | none |
| [Copernicus High Resolution Layer Imperviousness Density](#copernicus-imperviousness) | European Environment Agency, Copernicus Land Monitoring Service | 10 m | 2018 | package | none |
| [GPM IMERG Final Precipitation L3, daily](#imerg-daily) | NASA GES DISC | 0.1° (about 11 km) | 1998 | api | earthdata |
| [Global Historical Climatology Network - Daily (GHCN-Daily), rain gauges](#ghcn-daily) | NOAA National Centers for Environmental Information (NCEI) | point (rain gauge) | 1763 | api | none |
| [EOxCloudless 2016 (Sentinel-2 cloudless mosaic)](#eox-s2cloudless-2016) | EOX IT Services GmbH, from Copernicus Sentinel-2 | 10 m | 2016 | api | none |
| [NASA GIBS, Harmonized Landsat and Sentinel-2 (HLS) true colour](#nasa-gibs-hls) | NASA EOSDIS Global Imagery Browse Services (GIBS); HLS by NASA LP DAAC | 30 m | 2013 | api | none |
| [NASA VIIRS Black Marble annual](#black-marble) | NASA LAADS DAAC | ~500 m | 2012 | package | earthdata |
| [NASA JPL GRACE and GRACE-FO mascons CRI](#grace-fo) | NASA PO.DAAC and JPL | Native equal-area 3° mascons, distributed on a 0.5° grid | 2002 | package | earthdata |

<a id="landsat-c2-l2"></a>
## Landsat 8/9 Collection 2 Level-2

- Catalogue id: `landsat-c2-l2`; product `landsat-c2-l2`, version 02
- Licence: Public domain (USGS). No use restrictions; credit is optional. Cite doi:10.5066/P9OGBGM6.
- Citation: Landsat Collection 2 Level-2 image courtesy of the U.S. Geological Survey. doi:10.5066/P9OGBGM6
- Access: <https://planetarycomputer.microsoft.com/api/stac/v1/collections/landsat-c2-l2>

<a id="nasa-power"></a>
## NASA POWER

- Catalogue id: `nasa-power`; product `MERRA-2`
- Licence: Licence to be confirmed: the POWER referencing page names none. Acknowledgement required; send the reference of publications to the project; notify the POWER project if the data are redistributed. Do not imply NASA endorsement.
- Citation: The data was obtained from National Aeronautics and Space Administration (NASA) Langley Research Center's Prediction Of Worldwide Energy Resources (POWER) project funded through the NASA Earth Science Division
- Access: <https://power.larc.nasa.gov/api/temporal/monthly/point>

<a id="smap-l3"></a>
## SMAP L3 soil moisture 9 km

- Catalogue id: `smap-l3`; product `SPL3SMP_E`, version 006
- Licence: Open, without restriction (NASA NSIDC DAAC). Citing the specific product used is a condition of use. NSIDC asks to be told about publications that use the data. Do not imply NASA endorsement.
- Citation: SMAP L3 Soil Moisture (NASA NSIDC DAAC), SPL3SMP_E version 006, doi:[to be confirmed]
- Access: <https://nsidc.org/data/spl3smp_e>

<a id="chj-phj-2022-2027"></a>
## Plan Hidrológico de la Demarcación Hidrográfica del Júcar 2022-2027

- Catalogue id: `chj-phj-2022-2027`
- Licence: General reuse conditions for public-sector information (Ley 37/2007 and Real Decreto 1495/2011), per the CHJ legal notice. Cite the source and the update date; do not alter the meaning; do not suggest endorsement by the CHJ; keep the metadata.
- Citation: Origen de los datos: Confederación Hidrográfica del Júcar. Plan Hidrológico 2022-2027 (Real Decreto 35/2023), [document], p. [page], updated [date]
- Access: <https://www.chj.es/>
- Not redistributable as downloaded: the application serves only derived figures.

<a id="imerg-monthly"></a>
## GPM IMERG Final Precipitation L3, monthly

- Catalogue id: `imerg-monthly`; product `GPM_3IMERGM`, version 07
- Licence: NASA EOSDIS Data Use and Citation Guidance: openly shared, without restriction (CC0 by default for NASA-led mission data unless marked; the IMERG record names no licence). Acknowledge NASA GES DISC and cite the DOI. Do not imply NASA endorsement.
- Citation: GPM IMERG Final V07 (NASA GES DISC). Huffman, G. J., E. F. Stocker, D. T. Bolvin, E. J. Nelkin and J. Tan (2023), GPM IMERG Final Precipitation L3 1 month 0.1 degree x 0.1 degree V07, NASA GES DISC, Accessed: [date], doi:10.5067/GPM/IMERG/3B-MONTH/07
- Access: <https://doi.org/10.5067/GPM/IMERG/3B-MONTH/07>

<a id="nex-gddp-cmip6"></a>
## NASA Earth Exchange Global Daily Downscaled Projections (NEX-GDDP-CMIP6)

- Catalogue id: `nex-gddp-cmip6`; product `yearly-indicators`, version 2.0
- Licence: CC0 1.0 for all outputs since September 2022. Cite Thrasher et al. (2022), doi:10.7917/OFSG3345. Older files may still carry CC BY-SA in their metadata: use the current version. Do not imply NASA endorsement.
- Citation: NASA Earth Exchange Global Daily Downscaled Projections (NEX-GDDP-CMIP6) was accessed on [date] from https://registry.opendata.aws/nex-gddp-cmip6. Thrasher et al. (2022), doi:10.7917/OFSG3345
- Access: <https://www.nccs.nasa.gov/services/data-collections/land-based-products/nex-gddp-cmip6>

<a id="ghsl"></a>
## Global Human Settlement Layer population grid

- Catalogue id: `ghsl`; product `GHS-POP`, version R2023A V1-0
- Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). Attribution required and changes must be indicated. Cite Pesaresi et al. (2024), doi:10.1080/17538947.2024.2390454.
- Citation: GHSL R2023A (European Commission, JRC), CC BY 4.0. Pesaresi et al. (2024), doi:10.1080/17538947.2024.2390454
- Access: <https://doi.org/10.1080/17538947.2024.2390454>

<a id="copernicus-ems-emsr773"></a>
## Copernicus EMS observed flood extent, activation EMSR773

- Catalogue id: `copernicus-ems-emsr773`; product `EMSR773 Delineation`, version DEL v1
- Licence: Free access; reproduction, distribution and adaptation allowed provided the source is credited with the activation, the area of interest and the product type (Copernicus EMS terms and citation guidelines).
- Citation: Copernicus Emergency Management Service (© 2024 European Union), [EMSR773] [AOI]: Delineation
- Access: <https://mapping.emergency.copernicus.eu/activations/EMSR773/>

<a id="patricova"></a>
## PATRICOVA flood hazard

- Catalogue id: `patricova`; product `PATRICOVA flood hazard polygons`, version 2026-11-11 package
- Licence: Creative Commons Attribution; the Generalitat open-data record gives no version (the ICV conditions set CC BY 4.0 by default). Visible attribution required; anyone who receives the data or their derivatives accepts the same conditions.
- Citation: PATRICOVA [year] CC BY 4.0, Generalitat
- Access: <https://dadesobertes.gva.es/es/dataset/patricova-peligrosidad-por-inundacion-plan-de-accion-territorial-de-caracter-sectorial-sobre-pr>
- Not redistributable as downloaded: the application serves only derived figures.

<a id="ine"></a>
## INE census section boundaries

- Catalogue id: `ine`; product `Seccionado censal`, version 2026-01-01
- Licence: CC BY 4.0 unless otherwise stated (INE reuse conditions); the footer of that INE page says CC BY-SA 4.0, a contradiction pending clarification with INE. Cite the source and the update date; do not suggest endorsement by INE.
- Citation: Elaboración propia con datos extraídos del sitio web del INE: www.ine.es. Seccionado censal 2026 ([update date])
- Access: <https://www.ine.es/>
- Not redistributable as downloaded: the application serves only derived figures.

<a id="ine-population"></a>
## INE population by census section and official municipal figures

- Catalogue id: `ine-population`; product `Censo anual de población (población por sección censal, tabla 69274) y Revisión del Padrón Municipal (cifras oficiales por municipio, tabla 2903)`, version 2025
- Licence: CC BY 4.0 unless otherwise stated (INE reuse conditions); the footer of that INE page says CC BY-SA 4.0, a contradiction pending clarification with INE. Cite the source and the update date; do not suggest endorsement by INE.
- Citation: Elaboración propia con datos extraídos del sitio web del INE: www.ine.es. Censo anual de población a 1 de enero de 2025 (tabla 69274); Revisión del Padrón Municipal a 1 de enero de 2025 (tabla 2903)
- Access: <https://www.ine.es/jaxiT3/Tabla.htm?t=69274>
- Not redistributable as downloaded: the application serves only derived figures.

<a id="sedac-ssp"></a>
## SEDAC SSP population projections, 1/8 degree

- Catalogue id: `sedac-ssp`; product `Global 1/8-degree population base year and projection grids based on the SSPs`, version rev01
- Licence: Free to use, copy, distribute, transmit and adapt for commercial and non-commercial purposes, without restriction, as long as clear attribution of the source is provided (package readme; no Creative Commons licence named). Do not imply NASA endorsement.
- Citation: Population: Jones & O'Neill (2020), SSP 1/8-degree grids, rev. 01 (NASA SEDAC), doi:10.7927/m30p-j498
- Access: <https://doi.org/10.7927/m30p-j498>

<a id="jrc-gsw"></a>
## JRC Global Surface Water, water occurrence

- Catalogue id: `jrc-gsw`; product `occurrence`, version 1.4
- Licence: Free of charge and without restriction of use, within the Copernicus programme (GSW download page; no CC BY 4.0 named). Cite Pekel et al. (2016), doi:10.1038/nature20584.
- Citation: Source: EC JRC/Google. Pekel, Cottam, Gorelick and Belward (2016), Nature 540, doi:10.1038/nature20584
- Access: <https://global-surface-water.appspot.com/download>

<a id="boe-rd-35-2023"></a>
## Real Decreto 35/2023, de 24 de enero, Plan Hidrológico del Júcar (BOE-A-2023-3511)

- Catalogue id: `boe-rd-35-2023`
- Licence: Standard licence of the Agencia Estatal BOE (in force since 28 June 2024; boe.es legal notice). Cite and link boe.es; do not alter the meaning; do not present the content as official; give the date; flag modifications; keep the metadata. The official text is the one published at boe.es.
- Citation: Basado en datos de la Agencia Estatal Boletín Oficial del Estado. Real Decreto 35/2023, de 24 de enero, BOE núm. 35, de 10 de febrero de 2023 (BOE-A-2023-3511), https://www.boe.es/eli/es/rd/2023/01/24/35, consulted [date]
- Access: <https://www.boe.es/eli/es/rd/2023/01/24/35>

<a id="ghsl-built-s"></a>
## Global Human Settlement Layer built-up surface grid

- Catalogue id: `ghsl-built-s`; product `GHS-BUILT-S (total, 100 m tiles)`, version R2023A V1-0
- Licence: CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/); © European Union, 2023, reuse authorised provided the source is acknowledged (Decision 2011/833/EU); data provided as is, without warranty. Attribution required and changes must be indicated.
- Citation: GHS-BUILT-S R2023A (European Commission, JRC), doi:10.2905/9F06F36F-4B11-47EC-ABB0-4F8B7B1D72EA, CC BY 4.0. Pesaresi, M.; Politis, P. (2023); GHSL Data Package 2023, doi:10.2760/098587
- Access: <https://jeodpp.jrc.ec.europa.eu/ftp/jrc-opendata/GHSL/GHS_BUILT_S_GLOBE_R2023A/>

<a id="snczi"></a>
## Sistema Nacional de Cartografía de Zonas Inundables, flood hazard areas

- Catalogue id: `snczi`; product `Láminas de inundación por periodo de retorno (T10, T100, T500), tercer ciclo`, version 3Ciclo_20260727
- Licence: General reuse conditions of the Ministry (Ley 37/2007 and Real Decreto 1495/2011); no licence of its own on the SNCZI pages. Cite the source; do not suggest endorsement; keep the update date and the metadata.
- Citation: Fuente: Sistema Nacional de Cartografía de Zonas Inundables, MITECO (2026-06-01)
- Access: <https://gis.miteco.gob.es/descargas/app/DescargaFichero?f=laminasPB-q100.zip>
- Not redistributable as downloaded: the application serves only derived figures.

<a id="sentinel-1-rtc"></a>
## Sentinel-1 Radiometrically Terrain Corrected (RTC)

- Catalogue id: `sentinel-1-rtc`; product `VV gamma naught, IW mode, terrain flattened (Small 2011)`
- Licence: Copernicus Sentinel data licence: free, full and open access; attribution only, no share-alike and no use restrictions. The Microsoft Planetary Computer RTC collection is CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/): attribution required and changes must be indicated.
- Citation: Contains modified Copernicus Sentinel data [year]. Sentinel-1 RTC: Microsoft Planetary Computer, Catalyst, CC BY 4.0
- Access: <https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-1-rtc>

<a id="mur-sst"></a>
## GHRSST MUR SST v4.1, daily sea surface temperature

- Catalogue id: `mur-sst`; product `MUR-JPL-L4-GLOB-v4.1`, version 4.1
- Licence: NASA EOSDIS Data Use and Citation Guidance: openly shared, without restriction (CC0 by default for NASA mission data unless marked; the MUR v4.1 page names no licence). Cite the product and its DOI. Do not imply NASA endorsement.
- Citation: JPL MUR MEaSUREs Project (2015), GHRSST Level 4 MUR Global Foundation Sea Surface Temperature Analysis, version 4.1, PO.DAAC, https://doi.org/10.5067/GHGMR-4FJ04; Chin, Vazquez-Cuervo and Armstrong (2017), Remote Sensing of Environment 200. These data were provided by JPL under support by NASA MEaSUREs program
- Access: <https://mur-sst.s3.us-west-2.amazonaws.com/zarr>

<a id="icv-planeamiento"></a>
## Planeamiento urbanístico de la Comunitat Valenciana (clasificación del suelo)

- Catalogue id: `icv-planeamiento`; product `Planeamiento.Clasificacion (WFS 0702_Planeamiento)`
- Licence: Creative Commons Attribution (Generalitat Valenciana and ICV, according to datos.gob.es; the ICV conditions set CC BY 4.0 by default). Visible attribution required. Informative cartography without legal validity: it does not replace the approved planning documents.
- Citation: Institut Cartogràfic Valencià, Generalitat Valenciana: urban planning, land classification (WFS 0702_Planeamiento)
- Access: <https://terramapas.icv.gva.es/0702_Planeamiento>

<a id="chj-f71"></a>
## Red de control piezométrico de la CHJ (F71, Puntos de control piezométrico)

- Catalogue id: `chj-f71`; product `F71 Puntos de control piezométrico`, version 2026-11-13
- Licence: Reuse authorised for commercial and non-commercial purposes without specific conditions, under the general conditions of Ley 37/2007 and Real Decreto 1495/2011, art. 7, per the CHJ legal notice for datasets in datos.gob.es. Do not alter the meaning; cite the source and the update date; do not suggest endorsement by the CHJ; keep the metadata.
- Citation: Origen de los datos: Confederación Hidrográfica del Júcar. Red de control piezométrico (F71), point [code], downloaded [date]
- Access: <https://aps.chj.es/down/CSV/F71_Puntos_de_control_piezometrico.zip>

<a id="copernicus-imperviousness"></a>
## Copernicus High Resolution Layer Imperviousness Density

- Catalogue id: `copernicus-imperviousness`; product `Imperviousness Density (IMD), 10 m, EPSG:3035`, version 2018 v020 (10 m DOI 10.2909/3bf542bd-eebd-4d73-b53c-a0243f2ed862); 2021 and 2024 on WEkEO
- Licence: Copernicus Land Monitoring Service data policy: free, full and open access (Regulation (EU) No 1159/2013); no Creative Commons licence named. Inform of the source, indicate whether the data have been modified and do not suggest endorsement by the European Union.
- Citation: This publication has been prepared using European Union's Copernicus Land Monitoring Service information; Imperviousness Density 2018, https://doi.org/10.2909/3bf542bd-eebd-4d73-b53c-a0243f2ed862
- Access: <https://image.discomap.eea.europa.eu/arcgis/rest/services/GioLandPublic/HRL_ImperviousnessDensity_2018/ImageServer>

<a id="imerg-daily"></a>
## GPM IMERG Final Precipitation L3, daily

- Catalogue id: `imerg-daily`; product `GPM_3IMERGDF`, version 07
- Licence: NASA EOSDIS Data Use and Citation Guidance: openly shared, without restriction (CC0 by default for NASA-led mission data unless marked; the IMERG record names no licence). Acknowledge NASA GES DISC and cite the DOI. Do not imply NASA endorsement.
- Citation: GPM IMERG Final V07 (NASA GES DISC). Huffman, G. J., E. F. Stocker, D. T. Bolvin, E. J. Nelkin and J. Tan (2023), GPM IMERG Final Precipitation L3 1 day 0.1 degree x 0.1 degree V07, NASA GES DISC, Accessed: [date], doi:10.5067/GPM/IMERGDF/DAY/07
- Access: <https://doi.org/10.5067/GPM/IMERGDF/DAY/07>

<a id="ghcn-daily"></a>
## Global Historical Climatology Network - Daily (GHCN-Daily), rain gauges

- Catalogue id: `ghcn-daily`; product `GHCN-Daily PRCP`, version 3
- Licence: No licence named by NOAA NCEI. Data of non-U.S. stations (Spain included) are exchanged under WMO Resolution 40: free for research, education and other non-commercial use; neither the data nor derived products may be provided to others for commercial services. Cite the dataset and Menne et al. (2012). Not homogenised.
- Citation: GHCN-Daily, Version 3 (NOAA NCEI), station [code], doi:10.7289/V5D21VHZ, accessed [date]; Menne et al. (2012), doi:10.1175/JTECH-D-11-00103.1. Non-U.S. station data: free for research, education and other non-commercial use (WMO Resolution 40)
- Access: <https://www.ncei.noaa.gov/products/land-based-station/global-historical-climatology-network-daily>
- Not redistributable as downloaded: the application serves only derived figures.

<a id="eox-s2cloudless-2016"></a>
## EOxCloudless 2016 (Sentinel-2 cloudless mosaic)

- Catalogue id: `eox-s2cloudless-2016`; product `s2cloudless (2016 vintage), public WMS`, version 2016
- Licence: CC BY 4.0 for the 2016 vintage only (WMS capabilities and the EOxCloudless FAQ, read on 15 Nov 2026); the FAQ allows any use of the 2016 public endpoint with reasonable traffic, no SLA, and no resale of the raw tiles as a dataset. The credit must be visible wherever the image is shown and embedded in screenshots and exports. Vintages from 2018 are CC BY-NC-SA 4.0 and their public endpoint is not for production: not used.
- Citation: EOxCloudless https://cloudless.eox.at by EOX IT Services GmbH (Contains modified Copernicus Sentinel data 2016)
- Access: <https://tiles.maps.eox.at/wms>

<a id="nasa-gibs-hls"></a>
## NASA GIBS, Harmonized Landsat and Sentinel-2 (HLS) true colour

- Catalogue id: `nasa-gibs-hls`; product `HLS_S30_Nadir_BRDF_Adjusted_Reflectance and HLS_L30_Nadir_BRDF_Adjusted_Reflectance (WMS)`, version 2.0
- Licence: NASA EOSDIS Data Use and Citation Guidance: openly shared, without restriction. Acknowledge NASA GIBS and HLS; S30 contains modified Copernicus Sentinel data. Do not imply NASA endorsement.
- Citation: Imagery from NASA Global Imagery Browse Services (GIBS), part of NASA EOSDIS: Harmonized Landsat and Sentinel-2 ([product]), [date]. Contains modified Copernicus Sentinel data.
- Access: <https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi>

<a id="black-marble"></a>
## NASA VIIRS Black Marble annual

- Catalogue id: `black-marble`; product `VNP46A4`, version 002
- Licence: NASA EOSDIS open data. Cite the product.
- Citation: NASA VIIRS Black Marble VNP46A4 v002 (LAADS DAAC), doi:10.5067/VIIRS/VNP46A4.002.
- Access: <https://doi.org/10.5067/VIIRS/VNP46A4.002>

<a id="grace-fo"></a>
## NASA JPL GRACE and GRACE-FO mascons CRI

- Catalogue id: `grace-fo`; product `JPL GRACE/GRACE-FO Mascon CRI`, version RL06.3Mv04
- Licence: NASA EOSDIS open data. Cite the product.
- Citation: JPL GRACE and GRACE-FO RL06.3Mv04 CRI mascon solution (NASA PO.DAAC), doi:10.5067/TEMSC-3JC634.
- Access: <https://doi.org/10.5067/TEMSC-3JC634>
