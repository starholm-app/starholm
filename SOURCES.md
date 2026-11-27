# Library sources and licenses

The library is plain-text copies of the sources below, converted with `library/build_library.py`.
Each source keeps its own license, and the app shows it with every article. Text from Creative Commons
sources is shared under the same license (ShareAlike), credited to its authors: each article keeps its
source address, Stack Exchange posts and iFixit guides keep their authors' names, and this file travels
inside every library download. Licenses and terms were checked on 2026-10-24 and again on 2026-11-23.

Most sources come as ZIM files made by [Kiwix](https://kiwix.org) and [openZIM](https://openzim.org), who package
web content for offline reading. The content inside each file belongs to its original publisher, under the license
below.

## In the library

| Source | What it covers | License | Where it comes from |
|---|---|---|---|
| [Wikipedia](https://en.wikipedia.org), the most-read articles (about 930,000 in the free library, about 630,000 in the cleaned library after trimming) | General knowledge | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), Wikipedia contributors | Kiwix `wikipedia_en_top1m` |
| [WikiMed](https://en.wikipedia.org/wiki/Wikipedia:WikiProject_Medicine) | Medical encyclopedia (Wikipedia medicine) | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), Wikipedia contributors | Kiwix `wikipedia_en_medicine` |
| [MedlinePlus](https://medlineplus.gov) (NLM-written pages only) | Health topics, lab tests, genetics | Public domain (U.S. National Library of Medicine) | Kiwix `medlineplus.gov_en_all`; licensed sections removed (see below) |
| [UK Medicines A to Z](https://www.nhs.uk/medicines/) | How to take common medicines safely | Contains public sector information licensed under the [Open Government Licence v3.0](https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/). A static copy of the nhs.uk medicines pages as of September 2026; under the NHS website's terms it is not presented as the NHS website, and the NHS does not endorse Starholm. | Kiwix `nhs.uk_en_medicines` |
| [CDC Travelers' Health](https://wwwnc.cdc.gov/travel) | Disease, water and food safety | Public domain (CDC). Includes the online CDC Yellow Book; its printed edition is published by Oxford University Press. | Kiwix `wwwnc.cdc.gov_en_all` |
| [Ready.gov](https://www.ready.gov) (FEMA) | Emergency preparedness by hazard | Public domain (FEMA) | Kiwix `ready.gov_en_all` |
| [Military medicine manuals](https://irp.fas.org/doddir/milmed/) | Hospital Corpsman, First Aid (FM 4-25.11), Ship's Medicine Chest, field sanitation, preventive medicine, radiation protection | Public domain (U.S. government). Three volumes that reprint copyrighted material (Military Dermatology, War Psychiatry, FM 21-11) are left out. | Kiwix `irp.fas.org_en_military-medicine` |
| U.S. government survival and first-aid manuals | USAF AFH 10-644 SERE Operations, Army ATP 3-50.21 Survival, ATP 4-02.11 First Aid (2026), TC 21-3 Cold Weather, ATP 4-25.12 Field Sanitation, TB MED 577 Field Water, TB MED 507 Heat, FEMA Are You Ready?, FEMA CERT manual, Joint Trauma System Prolonged Casualty Care | Public domain (17 U.S.C. 105) | `library/fetch_manuals.py`, from [armypubs.army.mil](https://armypubs.army.mil), [e-publishing.af.mil](https://www.e-publishing.af.mil), [ready.gov](https://www.ready.gov) and [jts.health.mil](https://jts.health.mil) |
| [Nuclear War Survival Skills](https://www.ornl.gov) (Cresson H. Kearny, ORNL-5037, 1979; updated 1987 edition) | Fallout shelter, water, ventilation | The 1979 Oak Ridge National Laboratory report is a U.S. government work. The 1986 additions carry this notice, kept in the text: New material only: Copyright (c) 1986 by Cresson H. Kearny. "The copyrighted material may be reproduced without obtaining permission from anyone, provided: (1) all copyrighted material is reproduced full-scale (except for microfiche reproductions), and (2) the part of this copyright notice within quotation marks is printed along with the copyrighted material." | `library/fetch_manuals.py`, from ORNL |
| [Emergency Response Guidebook 2024](https://www.phmsa.dot.gov/training/hazmat/erg/emergency-response-guidebook-erg) | Hazardous materials spills and fires | Published by U.S. DOT/PHMSA, Transport Canada, Mexico's SICT and Argentina's CIQUIME. The guidebook says it "may be reproduced without further permission", that copies given free to emergency services may not be resold, and that a copy may show the governments' names and seals only if it reproduces the whole guidebook unchanged. The library holds its text only, without seals, and no government endorses Starholm. | `library/fetch_manuals.py` (from the Internet Archive; the official site refuses scripted downloads) |
| Risk and micromort references | Estimating risks of death: the Social Security life table (2023) restated in micromorts per year and per day, CDC's "Mortality in the United States, 2024" and "Deaths: Leading Causes for 2024", NHTSA's 2024 crash overview (deaths per mile driven), the National Weather Service's lightning odds; plus Wikipedia's articles on micromorts, Fermi estimates, life tables, relative risk and related topics | Government statistics: public domain (SSA, CDC, NHTSA, NWS). Wikipedia articles: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) | `library/fetch_risk.py` (CDC and SSA files from the Internet Archive; their sites refuse scripted downloads) |
| [USDA Complete Guide to Home Canning](https://nchfp.uga.edu/resources/category/usda-guide) (2015) | Safe canning, botulism prevention | Public domain (USDA) | Kiwix `usda-2015_en` |
| [Appropedia](https://www.appropedia.org) | Appropriate technology, sustainability | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/) by default; older pages [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/), as each page states; Appropedia contributors | Kiwix `appropedia_en_all` |
| [Energypedia](https://energypedia.info) | Solar, batteries, cookstoves, biogas | [CC BY-SA](https://creativecommons.org/licenses/by-sa/4.0/) 3.0 or 4.0, as each page states; Energypedia contributors | Kiwix `energypedia_en_all` |
| [WikiCiv](https://wikiciv.org) | Rebuilding technology from scratch | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), WikiCiv contributors | Kiwix `wikiciv_en_all` |
| [Gardenology](https://www.gardenology.org) | Plant encyclopedia | [CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/), Gardenology contributors, unless a page states otherwise; each article keeps its page address | Kiwix `gardenology_en_all` |
| [Stack Exchange](https://stackexchange.com): Home Improvement, The Great Outdoors, Seasoned Advice (cooking), Gardening, Lifehacks, Sustainable Living, Amateur Radio, Medical Sciences, Motor Vehicle Repair, Pets | Practical Q&A, with each post's author | [CC BY-SA](https://creativecommons.org/licenses/by-sa/4.0/) 2.5, 3.0 or 4.0 by post date ([Stack Exchange licensing](https://stackoverflow.com/help/licensing)) | Kiwix Stack Exchange ZIMs |

### Non-commercial licenses

These sources allow non-commercial use only. You may copy and share their text for non-commercial purposes, with
the credit and under the same license. Commercial reuse needs the publisher's permission. Starholm doesn't sell them:
their cleaned text comes free with the paid library, and the price covers the other sources and our work. Anyone can
also download them straight from their publishers with the free app.

| Source | What it covers | License | Where it comes from |
|---|---|---|---|
| [wikiHow](https://www.wikihow.com) (2018 text dump) | How-to guides | [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/), wikiHow contributors | Hugging Face `Lajvi/wikihowAll`, from the WikiHow dataset of Koupaee and Wang (2018) |
| [iFixit](https://www.ifixit.com) | Repair guides, with each guide's author | [CC BY-NC-SA 3.0](https://creativecommons.org/licenses/by-nc-sa/3.0/), iFixit and its guide authors | Kiwix `ifixit_en_all` |
| [IFRC International First Aid, Resuscitation and Education Guidelines 2020](https://www.ifrc.org/document/international-first-aid-resuscitation-and-education-guidelines) | Evidence-based first aid | © International Federation of Red Cross and Red Crescent Societies, Geneva, 2020. Copies may be made for non-commercial use, providing the source is acknowledged. The IFRC does not endorse Starholm. | `library/fetch_manuals.py` |
| WHO-ICRC Basic Emergency Care (2018); WHO Managing Complications in Pregnancy and Childbirth (2nd ed., 2017); WHO Guidelines for Drinking-water Quality (4th ed., 2022), all from [WHO's IRIS repository](https://iris.who.int) | Emergency care, childbirth, safe water | [CC BY-NC-SA 3.0 IGO](https://creativecommons.org/licenses/by-nc-sa/3.0/igo/). WHO and the ICRC do not endorse Starholm. | `library/fetch_manuals.py` |

### Collections with mixed rights

Kiwix's "zimgit" collections bundle PDFs from many publishers: government agencies, extension services, scouting
groups, companies and book publishers. No license is stated for the collections. Each document keeps its own
publisher's rights, and several are copyrighted books. Starholm doesn't sell them: they come free with the paid
library, and the price covers the other sources and our work. Anyone can also download them from Kiwix with the free
app's source picker.

| Collection | Documents | Kiwix file |
|---|---|---|
| Post Disaster Resource Library | About 58 | `zimgit-post-disaster_en` |
| Medical Library | About 11 | `zimgit-medicine_en` |
| Water Treatment Library | About 7 | `zimgit-water_en` |
| Food for Preppers | About 16 | `zimgit-food-preparation_en` |
| Library of Knots | About 9 | `zimgit-knots_en` |

## Not in the library today

- [WikEM](https://wikem.org) (emergency medicine, [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/),
  WikEM contributors): Kiwix no longer publishes its file, so current builds don't have it. The build code still
  accepts it if Kiwix brings it back.

## Left out on purpose

- Documents in languages other than English, from every source (translated pages, Spanish PDFs and the like); see
  `library/cleanup.py`. A source listed under `"off"` in `library/source-switch.json` is left out whole.
- MedlinePlus sections licensed to NLM by third parties: the A.D.A.M. Medical Encyclopedia
  (`/ency/`), ASHP drug information (`/druginfo/meds/`) and Natural Medicines (`/druginfo/natural/`).
- Military Dermatology and War Psychiatry (Textbook of Military Medicine) and FM 21-11 (1988): they reprint
  copyrighted figures and text. FM 4-25.11, which replaced FM 21-11, is included.
- Hesperian books (*Where There Is No Doctor* and others) as separate sources: digital use needs written permission.
- MSF clinical guidelines, the Sphere Handbook, the USAMRIID Blue Book, Red Cross manuals and
  commercial survival books: all rights reserved.

## Guides and country cards

Starholm's own guides and country emergency cards are written in Starholm's own words. Each one lists the sources
its facts come from at its foot, with the date they were checked. Those sources include the library sources above
and official pages such as the European Resuscitation Council guidelines, St John Ambulance, national emergency and
health services, and the [World Factbook Archive](https://worldfactbookarchive.org)'s copy of the CIA World Factbook
(a U.S. government work, public domain). Quoting a source as a reference does not mean the source endorses
Starholm.
