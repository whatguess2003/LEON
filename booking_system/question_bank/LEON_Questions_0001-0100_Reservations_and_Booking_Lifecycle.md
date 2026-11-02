# LEON — Question Bank 01: Reservations and Booking Lifecycle

*All example answers are fictional illustrations, not results from a live hotel database.*

1. **Question:** How many reservations are currently active for future stays?
   **Example answer:** There are 214 active reservations with at least one future stay night.

2. **Question:** How many reservations were created but never confirmed?
   **Example answer:** 19 reservations remain pending confirmation; 7 are older than 24 hours.

3. **Question:** Which reservations moved from pending to confirmed after their payment deadline?
   **Example answer:** Six reservations were confirmed after the stated deadline; the latest was 11 hours late.

4. **Question:** How many reservations were confirmed and then cancelled?
   **Example answer:** 23 reservations were cancelled after confirmation, representing 4.8% of confirmed bookings.

5. **Question:** What percentage of new reservations were modified at least once?
   **Example answer:** 12.6% of reservations created this month had one or more amendments.

6. **Question:** Which reservations have been amended most frequently?
   **Example answer:** Reservation R-1042 has six recorded amendments, the highest this month.

7. **Question:** How many bookings changed their check-in date after confirmation?
   **Example answer:** 14 confirmed bookings had their arrival date changed.

8. **Question:** Which reservations were shortened after the original confirmation?
   **Example answer:** Nine reservations lost a combined 21 room nights through shortened stays.

9. **Question:** Which reservations were extended after check-in?
   **Example answer:** Five in-house reservations were extended, adding eight room nights.

10. **Question:** How many bookings increased their room count after creation?
   **Example answer:** Seven reservations added rooms after their initial creation.

11. **Question:** Which future arrivals have no assigned room?
   **Example answer:** Eight upcoming reservations have confirmed room types but no room assignment.

12. **Question:** Which reservations have an assigned room that conflicts with another booking?
   **Example answer:** Two room assignments overlap on the same night and need correction.

13. **Question:** How many reservations contain more rooms than their booked room-type allocation?
   **Example answer:** Three reservations exceed their room-type allocation by one room each.

14. **Question:** Which bookings contain room assignments of a different type than the type sold?
   **Example answer:** Four bookings have a room-type mismatch; three are upgrades and one is a downgrade.

15. **Question:** Which reservations have a stay period crossing a hotel reporting month boundary?
   **Example answer:** 27 reservations span two reporting months.

16. **Question:** How many bookings have a check-out date that is not after check-in?
   **Example answer:** Two records have invalid stay-date order.

17. **Question:** Which reservations have an arrival date earlier than their creation date?
   **Example answer:** One backdated reservation requires verification.

18. **Question:** Which booking references are duplicated across different reservations?
   **Example answer:** Reference OTA-8831 appears on two reservation records.

19. **Question:** Which reservation records appear to describe the same stay and guest?
   **Example answer:** Three possible duplicate booking pairs share guest, dates and room type.

20. **Question:** Which reservations were imported from an OTA more than once?
   **Example answer:** Two OTA confirmation IDs have multiple import events but only one should remain active.

21. **Question:** How long does it typically take between reservation creation and confirmation?
   **Example answer:** Median confirmation time is 8 minutes; the 90th percentile is 2.4 hours.

22. **Question:** How many bookings were confirmed within five minutes of creation?
   **Example answer:** 81% of confirmed bookings were approved within five minutes.

23. **Question:** Which pending reservations are about to expire under their hold rules?
   **Example answer:** Four unpaid holds will expire within the next hour.

24. **Question:** What share of expired holds later converted into confirmed bookings?
   **Example answer:** 9 of 63 expired holds were subsequently reinstated and confirmed.

25. **Question:** How many confirmed bookings were created after their stated cancellation deadline?
   **Example answer:** Eleven reservations were booked after the applicable free-cancellation deadline.

26. **Question:** Which bookings were cancelled during the free-cancellation period?
   **Example answer:** 34 cancellations occurred before their penalty cutoff.

27. **Question:** How much time typically passes between booking and cancellation?
   **Example answer:** Median cancellation lead time is 2.1 days after reservation creation.

28. **Question:** Which reservations cancelled closest to their scheduled arrival?
   **Example answer:** Three cancellations occurred within two hours of check-in time.

29. **Question:** How many bookings were cancelled and later reinstated?
   **Example answer:** Five reservations were reinstated following cancellation.

30. **Question:** Which reservations have been cancelled more than once?
   **Example answer:** Two reservation histories contain multiple cancellation-and-reinstatement cycles.

31. **Question:** What percentage of reservations become no-shows after being confirmed?
   **Example answer:** 1.9% of confirmed arrivals ended as no-shows.

32. **Question:** Which no-show reservations were later marked checked in?
   **Example answer:** One no-show status was reversed to checked-in the following morning.

33. **Question:** How many guests arrived earlier than their scheduled check-in day?
   **Example answer:** Three arrivals were recorded a day earlier than originally booked.

34. **Question:** How many guests checked in after midnight for a previous-day reservation?
   **Example answer:** Seven late-night check-ins were posted after midnight against the prior business date.

35. **Question:** Which scheduled arrivals have no check-in event despite passing the cutoff time?
   **Example answer:** Six arrivals remain unaccounted for after the configured cutoff.

36. **Question:** How many reservations were checked in without a room assignment?
   **Example answer:** One check-in lacks a linked physical room.

37. **Question:** Which occupied reservations lack a valid check-in timestamp?
   **Example answer:** Four occupied stays have missing check-in event timestamps.

38. **Question:** How often is check-in completed before the standard check-in time?
   **Example answer:** 18% of arrivals checked in before 3 PM.

39. **Question:** How many reservations checked out later than the agreed departure time?
   **Example answer:** Nine stays departed more than one hour after their agreed checkout time.

40. **Question:** Which reservations remained marked in-house after their expected departure day?
   **Example answer:** Three stays still show in-house status beyond their scheduled departure.

41. **Question:** How long after checkout are reservation records usually closed?
   **Example answer:** Median time from checkout to administrative closure is 14 minutes.

42. **Question:** What share of completed stays underwent room changes?
   **Example answer:** 3.4% of completed stays had at least one in-house room move.

43. **Question:** Which reservations changed rooms more than once during a stay?
   **Example answer:** Two reservations involved three physical rooms each.

44. **Question:** How many bookings required a split stay across different rooms?
   **Example answer:** Six reservations were divided across rooms during their stay.

45. **Question:** Which reservations include two separately priced room types?
   **Example answer:** Four group bookings contain both Deluxe and Family room types.

46. **Question:** How many reservations were created directly at the front desk on arrival day?
   **Example answer:** 32 walk-in bookings were created on their check-in date.

47. **Question:** Which reservation channels produce the highest proportion of same-day bookings?
   **Example answer:** Walk-in bookings are 94% same-day, versus 27% for Booking.com.

48. **Question:** How many reservations were made within one hour of scheduled arrival?
   **Example answer:** Eleven bookings were created within one hour of check-in.

49. **Question:** What is the distribution of booking lead times for active arrivals?
   **Example answer:** 49% booked within two days, 31% within 3–14 days and 20% earlier.

50. **Question:** Which arrivals were booked more than six months in advance?
   **Example answer:** Four upcoming arrivals were booked over 180 days ahead.

51. **Question:** How many booking requests were rejected due to unavailable inventory?
   **Example answer:** Seventeen requests were rejected as sold out this month.

52. **Question:** Which rejected reservation requests later succeeded on another date?
   **Example answer:** Five failed requests converted after the guest changed dates.

53. **Question:** What percentage of quoted reservations were never completed?
   **Example answer:** 42% of issued quotes did not result in a confirmed booking.

54. **Question:** How long does a customer typically take to accept a reservation quote?
   **Example answer:** Accepted quotes took a median of 19 minutes to convert.

55. **Question:** Which reservation quotes expired without a response?
   **Example answer:** Twenty-two quotes reached expiry without acceptance or rejection.

56. **Question:** How frequently do agents override the original quoted room rate before confirmation?
   **Example answer:** 7.2% of confirmed bookings differ from their initial quoted rate.

57. **Question:** Which bookings required manual manager approval before confirmation?
   **Example answer:** Twelve bookings were confirmed following an approval event.

58. **Question:** How many bookings were made by staff on behalf of another customer?
   **Example answer:** Thirty-eight bookings have a creator different from the staying guest.

59. **Question:** Which reservations have no identifiable booking source?
   **Example answer:** Nine active bookings have a blank or unmapped source.

60. **Question:** How many reservations have incomplete mandatory guest contact details?
   **Example answer:** Fourteen future arrivals lack either a mobile number or valid email.

61. **Question:** Which upcoming group bookings have not supplied a rooming list?
   **Example answer:** Three group bookings need a rooming list before arrival.

62. **Question:** Which group reservations have fewer named guests than booked capacity?
   **Example answer:** Five groups have named occupants for only 61 of 84 booked beds.

63. **Question:** How many reservations combine consecutive stays under different booking references?
   **Example answer:** Eight guests have adjacent reservations that can be treated as continuous stays.

64. **Question:** Which guests hold overlapping reservations on the same dates?
   **Example answer:** Four guest profiles have overlapping active reservations.

65. **Question:** How many reservations were made with a company billing account?
   **Example answer:** Twenty-one active reservations use a corporate billing account.

66. **Question:** Which bookings were transferred from one company account to another?
   **Example answer:** Two bookings have changed their billed company.

67. **Question:** What percentage of reservations were paid by someone other than the staying guest?
   **Example answer:** 11% of recent bookings show a distinct payer account.

68. **Question:** How many reservations were booked with a package instead of a room-only rate?
   **Example answer:** Fifty-seven active reservations include a package code.

69. **Question:** Which reservations lost an included package component after modification?
   **Example answer:** Three modified bookings no longer have their original breakfast inclusion.

70. **Question:** How many reservations have inconsistent adult and child counts across rooms?
   **Example answer:** Six multiroom bookings have mismatched room-level and booking-level guest counts.

71. **Question:** Which reservations exceed the maximum stated occupancy of their room type?
   **Example answer:** Two upcoming reservations exceed configured room capacity.

72. **Question:** How many bookings requested connecting rooms but have nonadjacent assignments?
   **Example answer:** Four connecting-room requests are not satisfied by current assignments.

73. **Question:** Which reservations have special requests attached after check-in?
   **Example answer:** Twelve in-house reservations received new requests after arrival.

74. **Question:** How often do guests request a different bed configuration after booking?
   **Example answer:** Bed-configuration changes were requested on 6% of active reservations.

75. **Question:** Which future bookings have a room type that has since been retired?
   **Example answer:** Two bookings reference a no-longer-sellable room category.

76. **Question:** Which reservation amendments caused a change in cancellation policy?
   **Example answer:** Five amendments switched from flexible to nonrefundable terms.

77. **Question:** How many reservations have a deposit policy but no deposit due date?
   **Example answer:** Seven deposit-required bookings lack an enforceable due date.

78. **Question:** Which reservations have different booked and charged lengths of stay?
   **Example answer:** Three reservations have mismatched stay-night and charge-night counts.

79. **Question:** Which bookings contain zero-price room nights despite being paid reservations?
   **Example answer:** Four paid reservations include at least one zero-value room night.

80. **Question:** How many reservations were created while the chosen rate plan was inactive?
   **Example answer:** Two imported bookings used inactive rate plans.

81. **Question:** Which reservations have dates outside their contracted rate-plan validity?
   **Example answer:** Six bookings contain one or more out-of-contract stay nights.

82. **Question:** What share of reservations originate from repeat guests versus first-time guests?
   **Example answer:** Repeat guests account for 28% of active reservations.

83. **Question:** Which booking channels have the longest median booking lead time?
   **Example answer:** Corporate direct has a 29-day median lead time, the highest among channels.

84. **Question:** How many arrivals are linked to reservations created by a different property?
   **Example answer:** Three cross-property reservations are assigned to this hotel's arrivals.

85. **Question:** Which bookings have been moved from another hotel in the group?
   **Example answer:** Five incoming reservations were transferred from sister properties.

86. **Question:** How many reservations were partially cancelled rather than fully cancelled?
   **Example answer:** Seven multiroom reservations had one or more rooms cancelled.

87. **Question:** Which reservations contain rooms with different arrival dates?
   **Example answer:** Four reservations include staggered room arrivals.

88. **Question:** How many bookings contain separate departure dates for rooms under one reference?
   **Example answer:** Six group bookings have staggered room departures.

89. **Question:** Which reservations changed from single-room to multiroom stays?
   **Example answer:** Four bookings were expanded from one to multiple rooms.

90. **Question:** What share of reservations are extensions of an existing in-house stay?
   **Example answer:** Extensions make up 3.1% of newly created bookings.

91. **Question:** Which reservation events were posted out of chronological order?
   **Example answer:** Nine records have a status timestamp preceding the preceding event.

92. **Question:** Which bookings have missing audit history for a critical status change?
   **Example answer:** Three check-in or cancellation changes lack an associated audit event.

93. **Question:** How often are bookings manually reopened after checkout?
   **Example answer:** Four completed stays were reopened for correction this quarter.

94. **Question:** Which reservations were created during a system outage and entered later?
   **Example answer:** Eleven bookings were back-entered after the outage window.

95. **Question:** How many bookings have an OTA confirmation but no local PMS confirmation event?
   **Example answer:** Six imported bookings have no PMS confirmation audit event.

96. **Question:** Which reservations have conflicting statuses between PMS and their source channel?
   **Example answer:** Eight OTA reservations show a status mismatch requiring reconciliation.

97. **Question:** How many arrivals have an unacknowledged prearrival instruction message?
   **Example answer:** Thirteen future arrivals have sent instructions with no recorded acknowledgment.

98. **Question:** Which upcoming reservations still require identity verification?
   **Example answer:** Seventeen reservations have outstanding identity-verification checks.

99. **Question:** What proportion of reservations were created after a guest contacted support?
   **Example answer:** Twenty-four percent of confirmed bookings follow a linked inquiry within 48 hours.

100. **Question:** Which reservations were originally booked for a different room type due to oversell relocation?
   **Example answer:** Three bookings have documented oversell-driven room-type substitutions.
