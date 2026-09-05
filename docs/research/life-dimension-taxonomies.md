# Research: Life-Dimension Taxonomies (Quality-of-Life Frameworks)

Findings for the Personal Relevance Engine's Life Dimension taxonomy. Sources verified 2026-08-19; corrected rows re-verified 2026-09-05.

## Frameworks and their dimensions (primary sources)

| Framework | Dimensions |
|---|---|
| OECD Better Life Index (oecdbetterlifeindex.org) | Housing, Income, Jobs, Community, Education, Environment, Civic Engagement, Health, Life Satisfaction, Safety, Work-Life Balance |
| WHO WHOQOL-100 (who.int/tools/whoqol) | 6 domains: Physical health, Psychological, Level of independence, Social relationships, Environment, Spirituality/religion/personal beliefs |
| WHO WHOQOL-BREF (same source, abbreviated) | 4 domains: Physical, Psychological, Social relationships, Environment — independence folds into physical, spirituality into psychological |
| SAMHSA Eight Dimensions of Wellness (library.samhsa.gov SMA16-4958, Apr 2016) | Social, Environmental, Physical, Emotional, Spiritual, Occupational, Intellectual, Financial |
| Gallup Five Essential Elements (gallup.com/workplace/237020) | Career, Social, Financial, Physical, Community |
| Bhutan GNH Index (ophi.org.uk — Oxford Poverty & Human Development Initiative) | 9 domains: Psychological wellbeing, Health, Time use & balance, Education, Cultural diversity & resilience, Good governance, Community vitality, Ecological diversity & resilience, Living standards |
| Seligman PERMA / PERMA+ (UPenn Positive Psychology Center) | Positive emotion, Engagement, Relationships, Meaning, Accomplishment (+ Health) |
| Self-Determination Theory (Deci & Ryan) | Autonomy, Competence, Relatedness |
| Eurostat Quality of Life (ec.europa.eu/eurostat, statistics-explained) | 8+1: Material living conditions, Productive/main activity, Health, Education, Leisure & social interactions, Economic & physical safety, Governance & basic rights, Natural & living environment + Overall life experience |
| Coaching Wheel of Life (common 8-slice variant — no single canonical set; coaches use 6–12 segments) | career, finance, health, family & friends, romance, personal growth, fun & recreation, physical environment (+spirituality in variants) |

## Consensus clusters

1. **Physical health** — in 6 of 8 frameworks (OECD, WHOQOL, SAMHSA, GNH, Gallup, Eurostat). Unambiguous.
2. **Mental/psychological/emotional** — in 5 (WHOQOL, SAMHSA, GNH, PERMA, OECD-adjacent).
3. **Social & relationships** — in 7 (all except Eurostat's narrow framing; SDT calls it relatedness).
4. **Community & civic** — in 5 (OECD community + civic engagement, Gallup, GNH community vitality + governance, Eurostat).
5. **Career/work** — in 6 (OECD jobs, Gallup, SAMHSA occupational, GNH time-use, Eurostat productive activity, Wheel).
6. **Financial/material** — in 6 (OECD income, SAMHSA, Gallup, GNH living standards, Eurostat, Wheel).
7. **Education/learning** — in 4 (OECD, GNH, SAMHSA intellectual, Eurostat).
8. **Housing & environment** — housing in OECD/Eurostat; environment in OECD, SAMHSA, GNH, Eurostat, WHOQOL. Two distinct clusters actually: home + natural surroundings.
9. **Safety & security** — in 2 (OECD, Eurostat).
10. **Leisure/recreation/time balance** — in 4 (OECD work-life balance, GNH time use, Eurostat leisure, Wheel).
11. **Meaning/spirituality/purpose** — in 4 (WHOQOL SRPB, SAMHSA spiritual, PERMA meaning, Wheel).
12. **Autonomy/independence** — in 3 (WHOQOL independence, SDT autonomy, Eurostat governance & rights).
13. **Life satisfaction** — a meta-outcome (OECD, Eurostat overall experience), not a life area.

## Gap analysis vs our draft nine

Our nine: social, career, personal, relationship, family, business, reputational, housing, financial.

- **Covered**: social, career, financial, housing (partial).
- **Missing or buried under the catch-all "personal"**: physical health, mental/emotional health, community & civic, education/learning, environment & surroundings, safety & security, leisure/recreation, spirituality & meaning, autonomy & time.
- **Idiosyncratic-but-keep**: reputational (unique to the user's personal-brand business — no research framework has it; closest is PERMA accomplishment), business (research treats entrepreneur work as "career"; the user's split of career vs business is legitimate and higher-signal), family & relationship split (research folds both into "social"; the user's split gives better matching and extraction targets).
- **"Personal" is a catch-all that research decomposes** into physical health, mental health, learning, leisure, spirituality, autonomy — it should not survive as a dimension.

## Proposed consolidated taxonomy (17 dimensions)

Each backed by ≥2 frameworks unless marked user-specific:

1. **Physical Health** — fitness, nutrition, sleep, medical care, chronic conditions (OECD, WHOQOL, SAMHSA, GNH, Gallup, Eurostat)
2. **Mental & Emotional Wellbeing** — mood, stress, resilience, therapy (WHOQOL, SAMHSA, GNH, PERMA)
3. **Career** — role, skills, mobility, professional network, industry standing (OECD, Gallup, SAMHSA, PERMA)
4. **Business** — product, growth, sales, operations, team, legal (user-specific; Eurostat productive activity)
5. **Financial** — budgeting, saving/investing, debt, insurance, taxes, long-term (OECD, SAMHSA, Gallup, GNH, Eurostat)
6. **Social** — friendships, groups, gatherings, online communities (Gallup, SAMHSA, PERMA, SDT)
7. **Relationship** — romantic partnership (user-specific split; PERMA relationships)
8. **Family** — immediate, extended, parenting, elder care, household (user-specific split)
9. **Housing & Home** — operations, maintenance, move/search, neighborhood (OECD, Eurostat, WHOQOL environment)
10. **Community & Civic** — local community, volunteering, civic engagement (OECD, Gallup, GNH, Eurostat)
11. **Education & Learning** — formal learning, self-directed growth, skills (OECD, GNH, SAMHSA, Eurostat)
12. **Leisure & Recreation** — hobbies, fun, play, travel (OECD, GNH, Eurostat, Wheel)
13. **Environment & Surroundings** — natural surroundings, sustainability, home environment (OECD, SAMHSA, GNH, Eurostat)
14. **Safety & Security** — personal safety, digital security, economic security (OECD, Eurostat)
15. **Spirituality & Meaning** — purpose, values, faith, existential wellbeing (WHOQOL SRPB, SAMHSA, PERMA)
16. **Reputational** — public presence, content, mentions, credentials (user-specific; PERMA accomplishment adjacent)
17. **Autonomy & Time** — time sovereignty, schedule freedom, work-life balance, independence (GNH, WHOQOL, SDT, OECD)

Two structural notes:
- **Life satisfaction** is modeled as a per-dimension attribute (a satisfaction score on each of the 17), not a dimension — research treats it as an outcome of the others. Low satisfaction = high matching signal (openness to change).
- The threshold matrix becomes 2 digests × 17 dimensions = 34 cells, all self-calibrating from Verdicts — no day-one knobs either way.
