# Wildlife Tracker Mass Constraint

Recorded: 2026-10-17. Status: User-specified design requirement; installed
hardware mass has not been measured.

## Target animal

The tracker is intended for the Himalayan Vulture (*Gyps himalayensis*).
The user supplied an adult body-mass range of approximately 8–12 kg as
preliminary design context. The intended deployment age and the actual bird's
body mass have not yet been established.

**External reference:** Ma Ming et al. (2013), *BirdingASIA* 20: 84–89,
[Why are juvenile Himalayan Vultures Gyps himalayensis in the Xinjiang Tien Shan
still at the nest in October?](https://www.chinabirdnet.org/document/funding/china_conservation_fund/reports/Himalayan-Vultures_By%20MaMing.pdf)
reports adults weighing up to 12 kg, body length of 95–130 cm, and wingspan of
270–300 cm. It records a newly hatched chick at 164 g and one chick at 1,676 g
on 16 April and 8,720 g on 6 August 2013. These observations are external field
data, not project measurements or a species-wide growth curve. Adult mass must
not be assumed for a juvenile deployment.

## Required mass budget

The user requires the **entire installed tracking assembly** to weigh no more
than **3–5% of the individual bird's body mass**.

For design budgeting, use **3% as the conservative working limit** until the
deployment-specific percentage is established. Treat 5% as the upper boundary
of the supplied requirement, not as an automatically selected allowance.
This percentage is a project requirement supplied by the user; the growth
paper above does not establish it as a tagging safety threshold.

```text
installed_mass_g = tracker_electronics_g + battery_g + antennas_g
                 + enclosure_g + attachment_g + other_installed_parts_g
mass_fraction_percent = 100 * installed_mass_g / bird_body_mass_g
working_limit_g = 0.03 * bird_body_mass_g
upper_boundary_g = 0.05 * bird_body_mass_g
```

Include the PCB, modules, sensors, battery, GNSS and cellular antennas, wiring,
connectors, enclosure, waterproofing, harness or straps, fasteners, adhesives,
and any charging or solar components that remain attached to the bird.

**Illustrative calculations only; not measured device masses or deployment
approval:**

| Bird body mass | 3% working limit | 5% upper boundary |
| --- | --- | --- |
| 8 kg | 240 g | 400 g |
| 10 kg | 300 g | 500 g |
| 12 kg | 360 g | 600 g |

An adult design provisionally based on the supplied 8 kg lower bound therefore
has a **240 g total installed mass budget at 3%**. This is a preliminary budget,
not a fixed allowance for every bird. Recalculate for the actual individual;
do not use the 12 kg example to justify a heavier assembly on a lighter bird.

## Verification before deployment

Record the individual bird's measured body mass, measurement date, deployment
age or life stage, selected percentage, and the fully assembled installed
device mass. Calculate the resulting percentage and verify that it does not
exceed the selected limit. Weigh the complete assembly rather than relying
only on component datasheets; reserve mass margin while selecting components.

Open items: actual deployment bird and life stage, selected percentage within
the supplied range, component mass breakdown, and final assembled mass.
Meeting the mass requirement alone does not validate attachment fit or effects
on flight and behavior; these remain unverified deployment considerations.
