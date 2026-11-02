# LEON — 50 Hotel Management Test Questions

**Purpose:** Evaluate LEON's SQL recipe coverage, conversational interpretation, and ability to answer diverse hotel management questions. These questions intentionally vary calculations, relationships, filters, and reporting requirements—not merely phrasing. The objective is to determine how many distinct reusable capabilities the recipe library actually needs.

## A. Reservations & Booking Intelligence

1. How many reservations do we currently have for arrivals this week?
2. How many bookings were created yesterday, broken down by booking status?
3. What percentage of reservations made last month were subsequently cancelled?
4. What is our average booking lead time for guests arriving this month?
5. Which upcoming dates have the highest number of confirmed arrivals?

## B. Occupancy & Room Inventory

6. What was our occupancy rate for each day over the last 30 days?
7. Which room types had the highest occupancy last month?
8. How many rooms can we still sell tonight, excluding blocked and out-of-order rooms?
9. Which dates in the next 14 days are likely to reach full occupancy based on current reservations?
10. How many room nights did we lose because rooms were blocked or unavailable last month?

## C. Revenue & Hotel Performance

11. What was our total room revenue yesterday?
12. Show ADR, RevPAR, and occupancy for each day over the last seven days.
13. Which room type generated the most revenue last month?
14. How much did our room revenue increase or decrease compared with the previous month?
15. Which dates had high occupancy but below-average ADR?

## D. OTA & Distribution Performance

16. How much revenue did each booking channel generate last month?
17. Which OTA delivered the highest net revenue after commission?
18. Which booking channel had the highest cancellation rate over the last 90 days?
19. How does the ADR of direct bookings compare with OTA bookings?
20. Which OTA produced the most room nights relative to its share of total bookings?

## E. Guest Intelligence

21. How many guests are arriving today, including the total number of adults and children?
22. Which arriving guests have stayed with us before?
23. How many unique guests stayed with us last month?
24. Which repeat guests generated the most revenue over the last year?
25. Which upcoming guests previously submitted complaints or special requests?

## F. Payments & Financial Operations

26. How much outstanding payment is associated with guests currently staying?
27. Which guests checking out today still have unpaid balances?
28. How much payment did we actually collect yesterday, broken down by payment method?
29. How much revenue from last month remains uncollected?
30. Which reservations have discrepancies between their booking charges, payments, and outstanding balances?

## G. Housekeeping & Room Operations

31. How many rooms require cleaning today following guest departures?
32. Which arriving reservations are assigned to rooms that are not yet ready?
33. What was the average room turnaround time last week?
34. Which room types had the highest number of maintenance-related blocks last month?
35. How many rooms were marked ready after the standard check-in time during the last 30 days?

## H. Pricing & Revenue Management

36. How much does our current selling rate differ from our average achieved ADR for each room type?
37. How does booking pace for next weekend compare with the same lead-time position last weekend?
38. Which future dates have occupancy below our booking targets?
39. How much additional revenue would we generate if the remaining rooms tonight sold at their current rates?
40. Which room types experienced the largest changes in achieved ADR during the last 90 days?

## I. Staff, Tasks & Guest Service

41. How many operational tasks are currently overdue, grouped by responsible staff member?
42. What percentage of housekeeping tasks were completed on time last week?
43. Which guest requests have remained unresolved for more than two hours?
44. What is the average resolution time for guest complaints by category?
45. Which operational issues have been reopened or reported repeatedly during the last 30 days?

## J. Complex & Cross-Domain Reports

46. Which room types generated higher revenue this month despite selling fewer room nights than last month?
47. Which OTAs delivered bookings with above-average ADR but below-average length of stay?
48. How many rooms need to be cleaned and made ready today to accommodate all confirmed arrivals?
49. Which upcoming reservations combine an outstanding balance, an unresolved guest request, and an arrival within the next 48 hours?
50. Prepare a management performance report comparing the last seven days with the previous seven days, including occupancy, ADR, RevPAR, revenue, booking channels, cancellations, and outstanding payments.

---

## How to Use These Questions

**Do not assume 50 questions require 50 recipes.** A general recipe may answer multiple questions; a complex question may combine several recipes.

Classify each question after testing:

| Classification | Meaning |
| --- | --- |
| **Direct** | One existing recipe answers the question. |
| **Parameterized** | An existing recipe works with different dates, filters, or groupings. |
| **Composite** | Two or more recipes must be combined. |
| **New capability** | A genuinely different SQL procedure is required. |
| **Unsupported** | Required data does not exist in the current PMS. |

Some questions depend on housekeeping timestamps, guest counts, complaints, maintenance records, rate history, or payment details. If those fields are absent, classify the question as **Unsupported** rather than inventing SQL or figures.

For each question, record the **recipe IDs**, **SQL execution evidence**, **answer correctness**, **model calls**, **input/output tokens**, and **total response latency**. Preserve any clarifying questions and note whether the answers remained correct across follow-up turns.

### Benchmark Summary

- **Questions:** 50
- **Operational domains:** 10
- **Accuracy target:** 100% on supported, independently verifiable questions
- **Key research outcome:** Number of genuinely distinct recipes required, rather than the number of question phrasings

The outcome will help estimate whether LEON's mature recipe library requires approximately 100, 300, 1,000, or more reusable capabilities.
