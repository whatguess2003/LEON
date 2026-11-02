# LEON — Fragile Recipe Challenge

**Purpose:** Test the specific published recipes most likely to fail on meaning, arithmetic semantics, broad descriptions, or overlapping capabilities. These are **realistic simulated manager questions**, not claims that LEON has already answered them incorrectly.

**Protocol:** Submit each question to the real Desk Agent with the sandbox hotel's data. Prefer a fresh conversation for each question so earlier answers cannot contaminate the outcome. Save `question`, `reply`, `recipe IDs returned`, `SQL and parameters executed`, `rows`, and `latency`. Compare with independently calculated SQL/data and the user's intended business definition. If the answer is demonstrably wrong, send the appropriate natural correction/flag in that same conversation. **Only then** open an Investigator/developer ticket. If the data cannot support the question, an honest limitation is a valid outcome, not a recipe defect. Do not force a wrong-answer flag to manufacture a failure.

A recipe ID below is a **suspected target**, not a routing command: allow `get_recipe()` to select freely. A retrieval failure is different from defective recipe SQL.

## 1. `q09_full_occupancy_dates` — already full vs forecast to sell out

1. "Which nights over the next two weeks are most likely to sell out **before arrival**, even if they're not full right now?"
2. "We have three rooms left for Saturday. Based on booking pickup so far, is Saturday likely to reach 100% occupancy by check-in?"
3. "Rank next week's dates by **sellout risk**, and separate nights already sold out from nights only forecast to sell out."

**Check:** Does LEON distinguish *current sold-out status* from a forward-looking prediction? A current-occupancy query is insufficient. If no pickup/forecast history exists, it should state the limitation.

**Conditional user flag:** "That's not my question. You listed dates already full; I asked which dates are *likely to become full*. Please distinguish observed occupancy from a forecast."

## 2. `q42_housekeeping_tasks_on_time` — completion counts vs punctuality

4. "What percentage of housekeeping tasks due yesterday were **completed before their deadlines**?"
5. "Show tasks finished yesterday but **late**, including each deadline and actual completion time."
6. "Compare on-time housekeeping completion for morning and evening shifts last week, excluding tasks without a due time from the rate denominator."

**Check:** Must use deadline and completion timestamps, not merely `completed` status or task counts. Report tasks lacking sufficient timing data separately.

**Conditional user flag:** "You're counting completed tasks, but I asked for *on-time completion*. Check the due time against the finish time."

## 3. `q44_complaint_resolution_time_by_category` — counts vs elapsed time

7. "What was the **average time to resolve** housekeeping complaints versus noise complaints last month?"
8. "Show the median and longest resolution time for each complaint category, counting only complaints that actually closed."
9. "Which complaint category took longest to resolve last week, and how many complaints in that category remain open?"

**Check:** Measure elapsed time from appropriate open/received timestamp to resolved timestamp; group by category; distinguish closed vs unresolved cases.

**Conditional user flag:** "You returned complaint totals. I asked how long resolving each category takes."

## 4. `qb0701_hk_overtime_travel` — unrelated procedures bundled together

10. "How many **overtime hours** did each housekeeper work last Friday?"
11. "For guests moved to another room yesterday, what was the **median waiting time** between request and completed move?"
12. "Did the housekeeping team spend more overtime on days with more guest relocations last month? Show both series by date; don't assume one caused the other."

**Check:** Questions 10 and 11 should retrieve the appropriate sub-capability independently; question 12 requires a defensible temporal join and non-causal framing. Missing relocation timestamps must be acknowledged.

**Conditional user flag:** "I asked about housekeeping overtime, not guest relocation. You're using the wrong part of the report."

## 5. `qb0901_transfers` — coach capacity vs transport contribution

13. "For tomorrow's school groups, how many **coach bays** are required, and how many are actually reserved?"
14. "What was our **net margin on paid transfers** last month after driver pay, fuel and tolls?"
15. "Which group bookings need coach parking but have **no parking allocation**, regardless of whether they purchased a hotel transfer?"

**Check:** Parking capacity, parking allocation, paid transfer revenue, and transfer direct costs are distinct entities. Avoid substituting bus passenger counts for bay requirements.

**Conditional user flag:** "I asked about coach parking allocation. Transfer sales aren't the same thing."

## 6. `qb0201_sold_below_floor` — observed discount vs counterfactual loss

16. "How many room nights actually sold **below the approved rate floor**, and what was their total shortfall versus that floor?"
17. "How much revenue did we **definitely lose** by charging below the floor? Can our records establish that, or only the price difference?"
18. "Which below-floor bookings had a **documented authorized exception**, and which were unauthorized?"

**Check:** The price gap is measurable if historical floor and sold rate exist; realized lost demand/revenue is not proven by that gap. Authorization requires exception records.

**Conditional user flag:** "The gap against the rate floor isn't proven lost revenue. Please label it as a price difference, not money we definitely would have earned."

## 7. `qb0201_promo_yield` — promotional rate vs published-rate capacity

19. "What share of last month's **sold room nights** used a promotional rate?"
20. "Compare actual room revenue with the **sum of published rates for exactly the nights we sold**. Don't include empty rooms."
21. "What was the **theoretical published-rate capacity** for all sellable room nights, including unsold rooms, last weekend? Separate it from actual revenue."

**Check:** Distinguish realized sales, published price of sold nights, and total theoretical capacity. Do not describe their differences as guaranteed lost revenue.

**Conditional user flag:** "You mixed the value of unsold inventory with the discount on nights we actually sold. Those aren't the same measure."

## 8. `qb0901_service_recovery` — coincidence vs causation

22. "How many guest complaints about cleaning were logged after a room's **recorded turnaround exceeded two hours**?"
23. "How many **room nights were physically unsellable** because of maintenance on dates when the rest of the hotel sold out?"
24. "Can you calculate **proven revenue lost because of room downtime**, or only potential revenue at observed rates? State the assumptions."

**Check:** Temporal order matters; causation and unobserved counterfactual sales cannot be established from correlation alone.

**Conditional user flag:** "You presented potential revenue as confirmed lost revenue. Please separate historical facts from an estimate."

## 9. `daily_cancellation_counts` — three different date axes

25. "How many reservations were **cancelled yesterday**, irrespective of when they were booked or due to arrive?"
26. "How many bookings **scheduled to check in yesterday** are now cancelled, even if their cancellation happened earlier?"
27. "For the next seven stay nights, how many **room nights were removed** by cancellations made this week?"

**Check:** Cancellation event date, scheduled arrival date, and cancelled stay-night overlap produce different populations and counts.

**Conditional user flag:** "You counted cancellations by check-in date. I asked for the cancellations that happened yesterday."

## 10. `qb0201_revpar_bases` — denominator definition

28. "What was RevPAR per **physical room night** last week, including rooms out of order in the denominator?"
29. "What was RevPAR per **sellable room night** last week, excluding inventory legitimately blocked from sale?"
30. "Show both RevPAR definitions side by side for each day and explain why they differ on maintenance-blocked days."

**Check:** Numerator must be consistent; physical capacity and sellable inventory have different denominators. Ensure blocks aren't counted twice.

**Conditional user flag:** "I asked for physical-inventory RevPAR, but you used the sellable-room denominator."

## 11. `q29_period_revenue_uncollected` — stay-period revenue vs cash collections

31. "For room revenue **earned on last month's stay nights**, how much remains unpaid as of today, including partially paid stays?"
32. "A guest stayed last month but paid their invoice this month. Does that amount still count as **uncollected last-month revenue today**?"
33. "How much **cash was received last month** for stays in other months? Keep that separate from revenue earned last month."

**Check:** Revenue recognition period, current receivable balance, and receipt/payment date are different. Payment allocation to charges may be required.

**Conditional user flag:** "You used payment date instead of the month the room revenue was earned, so this isn't the receivables figure I requested."

## 12. `qb0101_restrictions` / `qb0301_stay_restrictions` — retrieval collision

34. "Which room types are **closed to arrival on Saturday**, but still allow guests already staying to remain that night?"
35. "Which dates next week have a **three-night minimum stay** but are **not** under stop-sell?"
36. "For next Friday, separate **stop-sell**, **closed to arrival**, and **minimum length of stay** restrictions by room type. Don't treat them as equivalent."

**Check:** The same subject is indexed in two recipes. The retrieved SQL must preserve each restriction's precise meaning; don't infer that a CTA restriction makes a room unsellable to stayovers.

**Conditional user flag:** "Closed to arrival doesn't mean a guest already checked in cannot stay. You're combining different restrictions."

## 13. `q23_unique_guests_stayed` — guest identity vs booking count

37. "How many **distinct people**, not reservations, stayed with us last month? Count a repeat guest once."
38. "If one booking contains two adults and a child, how many **guests** does it add to the total, and how many unique guest identities can we actually verify?"
39. "How many guests stayed on **more than one reservation** last month? Don't count the same person twice because they changed rooms."

**Check:** Count identifiable persons and distinguish them from booked party headcounts and reservation rows. When companions are not individually identified, don't claim precise unique-person counts.

**Conditional user flag:** "You counted bookings, not unique people. A guest can have several reservations."

## 14. `qb0501_verified_contact_conflicts` — shared contact vs duplicate identity

40. "Which guest profiles share the **same verified mobile number**, and how many are distinct profiles?"
41. "A parent and child share one phone number. Does that automatically mean their guest records are duplicates?"
42. "Which profiles have conflicting verified email or mobile details **across identity records**? Separate shared contact from proven duplicate people."

**Check:** Shared contact information is evidence for review, not proof of duplicate identity. Verify only facts actually present in records.

**Conditional user flag:** "You labeled two people as the same guest just because they share a phone number. That's not justified."

## 15. `qb0201_adr_weights` — different averaging grains

43. "What is ADR for last month calculated as **total room revenue divided by sold room nights**?"
44. "What is the **simple average of each reservation's own nightly average rate** for last month, so every booking has equal weight?"
45. "Show both averages for one-night and long-stay bookings, and explain which weighted average is appropriate for reporting hotel ADR."

**Check:** Night-weighted ADR and unweighted mean of booking-level ADR are different computations. They may match coincidentally on uniform stays; use a varied-rate fixture if possible.

**Conditional user flag:** "You returned standard room-night-weighted ADR, but I explicitly asked for each reservation to receive equal weight."

---

## Failure handling — user flag is the only ordinary recipe-fix trigger

1. Ask a question naturally. Do not force a particular recipe or supply a desired result.
2. Obtain LEON's actual answer and tool execution evidence.
3. Determine whether the answer is wrong relative to the supported business meaning and database. Classify **correct / wrong / ambiguous / unsupported / unverifiable**.
4. **Only if wrong**, send a natural user correction similar to the conditional flag and invoke the existing flagged-answer investigation path.
5. Investigator checks conversation context → retrieval → recipe → SQL and parameters → data → explanation, then files a developer fix only when the defect is verified.
6. Developer repairs or adds a recipe and tests the original question, the other two variants in that group, and relevant previous questions. Keep the normal Desk path unchanged.

**Do not:** automatically flag a valid refusal, request a developer fix merely because a user complained, prelabel these 45 questions as failures, or create new recipes when the real defect is context, indexing, or insufficient PMS data.
