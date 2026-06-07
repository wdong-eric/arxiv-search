# Zotero Library Migration Guide

Generated from a read-only snapshot of `/Users/Eric.Dong/Zotero/zotero.sqlite`.

No live Zotero data was edited. The live database was locked, so analysis used a temporary copy at `/private/tmp/zotero-readonly-copy.sqlite`.

## Snapshot

- Bibliographic items, excluding notes, annotations, and attachments: 1,288.
- Collections: 62.
- Tags: 958.
- Items with abstracts: 1,241.
- Unfiled bibliographic items: 4.
- Collection usage is already strong: 870 items are in exactly one collection, 308 in two, 75 in three, 25 in four, and 6 in five.

The current library has three useful axes mixed together:

1. Workflow/reference status: `0.0 - books`, `0.1 - review papers`, `0.2 - to be read`, `read abstract & interesting`, `0 - my papers`.
2. Active research programs and paper-specific reading piles: `0.3 - projects`, `paper - tauUTMOST`, `paper - rmode`, `paper-bursdp`, `paper - Dong_Melatos2024`.
3. Durable scientific themes: `modes/seismic`, `magnetic stuff`, `EOS`, `PBH`, `data analysis`, `tidal`, `stochastic processes`, etc.

The main migration should separate these axes rather than replacing the existing organisation wholesale.

## Recommended Design

Use collections for "where this paper belongs in my work" and tags for cross-cutting properties.

Collections should answer one primary question: why would I open this group?

Tags should answer secondary questions: what physics, method, object class, status, or priority also applies?

### Proposed Top-Level Collections

```text
00 Workflow
01 Reference Shelf
02 Projects
03 Topics
04 Methods and Data
05 People and Groups
99 Archive
```

### Proposed Tree

```text
00 Workflow
  Inbox - To Read
  Skimmed - Interesting
  Priority - Starred

01 Reference Shelf
  Books
  Review Papers
  My Papers

02 Projects
  Continuous Waves and GWOANS
    CW Searches
    Dong-Melatos 2024
    r-mode Paper
      Response Letter
  Pulsar Timing
    tauUTMOST
      Response
    tauToRadius
    Red-to-White Noise
    Pulsar Population
  Accretion and Bursts
    Burst Paper
    Type I Bursts
    Efficiency and Outflows
    Magnetic Arrested Accretion
    Torque and Inflow
  GR and Gravity
    QNM and Ringdown
    Binding and LISA
    Energy and Quasilocal Mass
    Speculative Edge Cases
  Glitches, PTA, and Timing Noise
    Glitch GW
  PBH in Neutron Stars
    Oscillating NS with PBH
    Asteroid PBH Accretion

03 Topics
  Neutron Star Oscillations and Seismology
    General Modes and Oscillations
    r-modes and Rotation
    Excitation
    Instability
    Nonlinear Coupling
    Crust and Magnetar Seismology
  Magnetic Fields and MHD
    Magnetic Mountains and Accretion Columns
    Magnetospheres
    Magnetic Field Evolution
  Dense Matter and Interiors
    EOS
    Crust
    Surface Temperature
  Gravitational Waves and Transients
    Bursts and Transients
    Continuous Waves
    PTAs
  Tides
  Primordial Black Holes

04 Methods and Data
  Data Analysis
  Data Catalogues
  Numerical Schemes
  Stochastic Processes
  Green Functions
  Multi-Scale Methods

05 People and Groups
  AM Group

99 Archive
  Assorted
  External / EXTP
  Philosophy
```

## Collection Migration Map

Counts are direct bibliographic item memberships in the current collection.

| Current collection | Count | Proposed destination | Action |
|---|---:|---|---|
| `0.3 - projects` | 0 | `02 Projects` | Rename top-level parent. |
| `0.3 - projects / 1 - GWOANS/CW/GW` | 14 | `02 Projects / Continuous Waves and GWOANS` | Rename. Keep direct items here if they support the whole program rather than one paper. |
| `0.3 - projects / 1 - GWOANS/CW/GW / CW searches` | 19 | `02 Projects / Continuous Waves and GWOANS / CW Searches` | Rename. |
| `0.3 - projects / 1 - GWOANS/CW/GW / paper - Dong_Melatos2024` | 95 | `02 Projects / Continuous Waves and GWOANS / Dong-Melatos 2024` | Rename. |
| `0.3 - projects / 1 - GWOANS/CW/GW / paper - rmode` | 108 | `02 Projects / Continuous Waves and GWOANS / r-mode Paper` | Rename. |
| `0.3 - projects / 1 - GWOANS/CW/GW / paper - rmode / response_letter` | 60 | `02 Projects / Continuous Waves and GWOANS / r-mode Paper / Response Letter` | Rename. |
| `0.3 - projects / 2 - psrtim` | 0 | `02 Projects / Pulsar Timing` | Rename. |
| `0.3 - projects / 2 - psrtim / paper - tauUTMOST` | 138 | `02 Projects / Pulsar Timing / tauUTMOST` | Rename. |
| `0.3 - projects / 2 - psrtim / paper - tauUTMOST / response` | 40 | `02 Projects / Pulsar Timing / tauUTMOST / Response` | Rename. |
| `0.3 - projects / 2 - psrtim / tauToRadius` | 13 | `02 Projects / Pulsar Timing / tauToRadius` | Rename. |
| `0.3 - projects / 2 - psrtim / psr_RedToWhite` | 8 | `02 Projects / Pulsar Timing / Red-to-White Noise` | Rename. |
| `0.3 - projects / 2 - psrtim / psr_pop` | 11 | `02 Projects / Pulsar Timing / Pulsar Population` | Rename. |
| `0.3 - projects / 3 - accretion` | 134 | `02 Projects / Accretion and Bursts` | Rename. Keep broad accretion references here. |
| `0.3 - projects / 3 - accretion / paper-bursdp` | 58 | `02 Projects / Accretion and Bursts / Burst Paper` | Rename. |
| `0.3 - projects / 3 - accretion / burst-type1` | 90 | `02 Projects / Accretion and Bursts / Type I Bursts` | Rename. |
| `0.3 - projects / 3 - accretion / efficiency/outflow` | 54 | `02 Projects / Accretion and Bursts / Efficiency and Outflows` | Rename. |
| `0.3 - projects / 3 - accretion / magnetic arrested` | 12 | `02 Projects / Accretion and Bursts / Magnetic Arrested Accretion` | Rename. |
| `0.3 - projects / 3 - accretion / torque/inflow` | 31 | `02 Projects / Accretion and Bursts / Torque and Inflow` | Rename. |
| `0.3 - projects / GR/gravity` | 12 | `02 Projects / GR and Gravity` | Rename. |
| `0.3 - projects / GR/gravity / qnm/ringdown` | 45 | `02 Projects / GR and Gravity / QNM and Ringdown` | Rename. |
| `0.3 - projects / GR/gravity / binding-lisa` | 5 | `02 Projects / GR and Gravity / Binding and LISA` | Rename. |
| `0.3 - projects / GR/gravity / energy` | 36 | `02 Projects / GR and Gravity / Energy and Quasilocal Mass` | Rename. |
| `0.3 - projects / GR/gravity / quasilocal m loss` | 4 | `02 Projects / GR and Gravity / Energy and Quasilocal Mass` | Merge into `Energy and Quasilocal Mass`; use a `quasilocal-mass` tag if needed. |
| `0.3 - projects / GR/gravity / wacky` | 25 | `02 Projects / GR and Gravity / Speculative Edge Cases` | Rename to make the purpose searchable. |
| `0.3 - projects / glitchGW` | 11 | `02 Projects / Glitches, PTA, and Timing Noise / Glitch GW` | Move under the consolidated project family. |
| `glitch & pta & psr tim` | 100 | `02 Projects / Glitches, PTA, and Timing Noise` | Move under projects. Split with tags rather than new folders unless this grows again. |
| `PBH` | 1 | `02 Projects / PBH in Neutron Stars` or `03 Topics / Primordial Black Holes` | If this is an active workstream, use `02 Projects`; otherwise use `03 Topics`. |
| `PBH / Osc of NS with PBH inside` | 24 | `02 Projects / PBH in Neutron Stars / Oscillating NS with PBH` | Rename. |
| `PBH / How asteroid PBH accret?` | 6 | `02 Projects / PBH in Neutron Stars / Asteroid PBH Accretion` | Rename. |
| `modes/seismic` | 5 | `03 Topics / Neutron Star Oscillations and Seismology` | Rename top-level parent. |
| `modes/seismic / mode/oscillations` | 69 | `03 Topics / Neutron Star Oscillations and Seismology / General Modes and Oscillations` | Rename. |
| `modes/seismic / r-mode & rotation-related` | 87 | `03 Topics / Neutron Star Oscillations and Seismology / r-modes and Rotation` | Rename. |
| `modes/seismic / excitation` | 24 | `03 Topics / Neutron Star Oscillations and Seismology / Excitation` | Rename. |
| `modes/seismic / (in)stability` | 21 | `03 Topics / Neutron Star Oscillations and Seismology / Instability` | Rename. |
| `modes/seismic / coupling/nonlinearity` | 19 | `03 Topics / Neutron Star Oscillations and Seismology / Nonlinear Coupling` | Rename. |
| `magnetic stuff` | 88 | `03 Topics / Magnetic Fields and MHD` | Rename. Later split with tags if the collection becomes too broad. |
| `EOS` | 15 | `03 Topics / Dense Matter and Interiors / EOS` | Move. |
| `crust` | 18 | `03 Topics / Dense Matter and Interiors / Crust` and/or `03 Topics / Neutron Star Oscillations and Seismology / Crust and Magnetar Seismology` | Prefer `Dense Matter and Interiors / Crust`; add tag `seismology` when relevant. |
| `surface_temp` | 8 | `03 Topics / Dense Matter and Interiors / Surface Temperature` | Rename and move. |
| `burst/transients` | 16 | `03 Topics / Gravitational Waves and Transients / Bursts and Transients` | Rename and move. |
| `GWs` | 1 | `03 Topics / Gravitational Waves and Transients` | Merge into parent. |
| `tidal` | 10 | `03 Topics / Tides` | Rename. |
| `data analysis` | 31 | `04 Methods and Data / Data Analysis` | Move. |
| `data catalogue` | 8 | `04 Methods and Data / Data Catalogues` | Rename and move. |
| `numerical scheme` | 7 | `04 Methods and Data / Numerical Schemes` | Rename and move. |
| `stochastic processes` | 11 | `04 Methods and Data / Stochastic Processes` | Move. |
| `green function` | 4 | `04 Methods and Data / Green Functions` | Rename and move. |
| `multi-scale related` | 7 | `04 Methods and Data / Multi-Scale Methods` | Rename and move. |
| `AM group` | 37 | `05 People and Groups / AM Group` | Move. |
| `0.0 - books` | 35 | `01 Reference Shelf / Books` | Rename and move. |
| `0.1 - review papers` | 49 | `01 Reference Shelf / Review Papers` | Rename and move. |
| `0 - my papers` | 4 | `01 Reference Shelf / My Papers` | Rename and move. |
| `0.2 - to be read` | 17 | `00 Workflow / Inbox - To Read` | Rename and move. |
| `0.2 - to be read / accretion` | 21 | `00 Workflow / Inbox - To Read` | Move items to parent and tag `topic:accretion`; or keep as `00 Workflow / Inbox - To Read / Accretion` if you actively triage by topic. |
| `0.2 - to be read / seismology` | 21 | `00 Workflow / Inbox - To Read` | Move items to parent and tag `topic:seismology`; or keep as `00 Workflow / Inbox - To Read / Seismology`. |
| `0.2 - to be read / energy` | 8 | `00 Workflow / Inbox - To Read` | Move items to parent and tag `topic:energy`. |
| `0.2 - to be read / numerical` | 5 | `00 Workflow / Inbox - To Read` | Move items to parent and tag `method:numerical`. |
| `0.2 - to be read / burst` | 2 | `00 Workflow / Inbox - To Read` | Move items to parent and tag `topic:bursts`. |
| `read abstract & interesting` | 23 | `00 Workflow / Skimmed - Interesting` | Rename and move. |
| `EXTP` | 2 | `99 Archive / External / EXTP` | Move. |
| `assorted` | 7 | `99 Archive / Assorted` | Move, then drain over time. |
| `z~philosophy` | 7 | `99 Archive / Philosophy` | Rename and move. |

## Tag Migration

The tags are useful but noisy. Many appear to be imported subject headings or arXiv categories. Do not use them as the primary folder system.

### Keep These Tag Families

Use lowercase, namespaced tags where you intentionally assign meaning:

```text
status:read
status:to-read
status:skimmed
priority:1
priority:2
priority:3
priority:4
priority:5
topic:neutron-stars
topic:accretion
topic:r-modes
topic:pulsars
topic:gravitational-waves
topic:eos
topic:pbh
topic:magnetic-fields
method:bayesian-inference
method:numerical
method:green-functions
method:stochastic-processes
object:neutron-star
object:white-dwarf
object:black-hole
project:gwoans
project:tauutmost
project:burst-paper
project:pbh-ns
```

### Normalize These Existing Tags

| Existing variants | Suggested canonical tag |
|---|---|
| `Neutron stars`, `Neutron Stars`, `neutron stars`, `STARS: NEUTRON`, `stars: neutron` | `object:neutron-star` |
| `Pulsars`, `PULSARS`, `pulsars`, `pulsars: general` | `object:pulsar` |
| `ACCRETION`, `Accretion`, `accretion` | `topic:accretion` |
| `ACCRETION DISKS`, `Accretion Disks`, `accretion disks` | `topic:accretion-disks` |
| `Stellar Oscillations`, `STARS: OSCILLATIONS`, `Oscillations` | `topic:oscillations` |
| `Gravitational Waves`, `Gravitational waves`, `gravitational waves`, `GW` | `topic:gravitational-waves` |
| `Stellar Rotation`, `STARS: ROTATION`, `rotation` | `topic:rotation` |
| `Magnetohydrodynamics`, `MAGNETOHYDRODYNAMICS: MHD`, `MHD` | `topic:mhd` |
| `Dense Matter`, `DENSE MATTER`, `dense matter` | `topic:dense-matter` |
| `Equations Of State` | `topic:eos` |
| `Physics - Fluid Dynamics`, `Fluid dynamics`, `HYDRODYNAMICS` | `method:fluid-dynamics` |

### Star Tags

The current star tags are useful enough to preserve but should be made unambiguous:

| Current tag | Suggested tag |
|---|---|
| `⭐` | `priority:1` |
| `⭐⭐` | `priority:2` |
| `⭐⭐⭐` | `priority:3` |
| `⭐⭐⭐⭐` | `priority:4` |
| `⭐⭐⭐⭐⭐` | `priority:5` |

If you like the visual stars in Zotero, keep them, but treat them as a UI convenience rather than the canonical taxonomy.

## Migration Procedure

1. In Zotero, make a full backup or export before doing anything.
2. Create the six proposed top-level collections.
3. Rename and move existing collections according to the migration map.
4. Do not duplicate a paper into many topic collections just because it has several subjects. Put it in the most useful project/topic collection, then use tags for secondary facets.
5. For papers currently in four or five collections, manually review them first. These are mostly high-value bridge papers across r-modes, accretion, response letters, and seismology.
6. Move `0.2 - to be read` subfolders into `00 Workflow / Inbox - To Read` unless you strongly prefer topic-specific triage.
7. Normalize tags gradually. Start with the duplicated high-frequency tags listed above.
8. Leave imported arXiv category tags such as `Astrophysics - High Energy Astrophysical Phenomena` alone unless they are actively annoying. They are not harming the collection taxonomy.
9. After the move, use Zotero's Duplicate Items and Unfiled Items views. The current snapshot has only 4 unfiled bibliographic items, so any large unfiled count after migration likely means something went wrong.

## Manual Review List

Prioritize these areas during migration:

- Items currently in four or five collections. They are the highest risk for accidental semantic duplication.
- `magnetic stuff`, because the name hides a real theme and the collection spans magnetars, magnetic mountains, magnetospheres, MHD, and magnetic field evolution.
- `glitch & pta & psr tim`, because it overlaps with `Pulsar Timing` but also includes GW/PTA theory and glitch physics.
- `0.2 - to be read`, because it is a workflow state, not a permanent scientific category.
- `PBH`, because it can be either an active project family or a general topic. The current subcollections suggest it is closer to an active project.

## Expected End State

After migration, the library should have:

- A stable project tree that mirrors your actual research programs.
- A topic tree for durable scientific areas independent of current papers.
- A small workflow area for triage and reading state.
- A reference shelf for books, reviews, and your own papers.
- Cleaner tags for cross-cutting concepts without forcing every paper into many collections.

This preserves your current logic while making the distinction between project, topic, method, and workflow explicit.
