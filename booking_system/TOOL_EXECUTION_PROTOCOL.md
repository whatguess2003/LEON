# LEON PMS Tool Execution Protocol (Gold Standard)

## Overview
This document defines the gold standard for all tool execution in the LEON PMS system. Every tool must follow this 3-step protocol:

1. **Extraction**: Extract required parameters using the LLM-powered `unified_parser` (from `llm_utils.py`).
2. **Validation**: Check for missing/ambiguous parameters and business rule compliance. Return a structured response for the orchestrator to prompt the user.
3. **Execution**: Only execute business logic if all parameters are present and user confirmation is detected, using `unified_execution_detector` (from `llm_utils.py`).

No LLM calls are allowed outside `llm_utils.py`.

---

## 1. Extraction
- Use `unified_parser` to extract only the parameters needed for the tool.
- The tool defines the parameter list and extraction instruction.
- Output: Dict of extracted parameters (empty string for missing/ambiguous).

**Example:**
```python
param_list = ["guest_phone", "room_type", "check_in_date", "nights", "rooms_booked", "booking_notes"]
instruction = "Extract guest_phone, room_type, check_in_date, nights, rooms_booked, and booking_notes for a new booking."
extracted = unified_parser(user_input, dialog_history, param_list, instruction)
```

---

## 2. Validation
- Check which required parameters are missing or ambiguous (empty string).
- If any are missing, return:

```python
{
    "status": "incomplete",
    "missing": ["check_in_date", "guest_phone"],
    "extracted": { ... }
}
```
- If all are present, but business validation fails, return:
```python
{
    "status": "validation_failed",
    "issues": ["Room type does not exist", "Guest profile does not exist"],
    "extracted": { ... }
}
```

---

## 3. Execution
- If all parameters are present, check for explicit user confirmation using `unified_execution_detector`:
```python
if not user_confirmation or unified_execution_detector(user_confirmation) != "execute":
    return {
        "status": "awaiting_confirmation",
        "extracted": extracted
    }
```
- Only execute business logic if confirmation is detected. Return:
```python
{
    "status": "executed",
    "result": "✅ Booking created for Deluxe Suite on 2024-07-12 for +60123456789."
}
```

---

## Orchestrator Integration
- The orchestrator receives only the structured response from the tool.
- It prompts the user for missing parameters, requests confirmation, or displays results based on the `status` field.
- The orchestrator never sees raw LLM output or internal tool logic.

---

## Frontend Integration & UI/UX Mapping

The 3-step protocol is designed for seamless integration with modern frontend frameworks (React, Vue, etc.).

### Extraction → Dynamic Form Fields
- The backend returns a list of required parameters and their extracted values.
- The frontend renders a form with fields for each parameter in `param_list`.
- Fields with empty values are shown as empty or highlighted for user input.
- Pre-filled fields (from extraction) are shown as green/valid, empty as red/invalid.

### Validation → Real-Time Feedback
- If the backend returns `"status": "incomplete"`, the frontend:
  - Highlights missing/ambiguous fields in red.
  - Shows valid fields in green.
  - Optionally displays tooltips or inline messages for missing fields.
- If the backend returns `"status": "validation_failed"`, the frontend:
  - Displays business rule errors (e.g., “Room type does not exist”) as inline messages or tooltips.
  - Guides the user to correct only the problematic fields.

### Execution → Confirmation Button
- If the backend returns `"status": "awaiting_confirmation"`, the frontend:
  - Enables the “OK” or “Confirm” button only when all required fields are filled and validation passes.
  - On click, sends a confirmation message/input to the backend.
- If the backend returns `"status": "executed"`, the frontend:
  - Shows a success message or booking confirmation.

### UI/UX Mapping Table
| Backend Status         | Frontend Action/UX                                 |
|-----------------------|----------------------------------------------------|
| incomplete            | Highlight missing fields, prompt for input         |
| validation_failed     | Show business rule errors, prompt to fix           |
| awaiting_confirmation | Enable “OK”/“Confirm” button, wait for user action |
| executed              | Show success/confirmation message                  |

### Example UI Flow
1. User starts a booking.
2. Frontend sends initial input to backend.
3. Backend returns:
   ```json
   {
     "status": "incomplete",
     "missing": ["check_in_date", "guest_phone"],
     "extracted": {
       "guest_phone": "",
       "room_type": "Deluxe Suite",
       "check_in_date": "",
       "nights": "2",
       "rooms_booked": "1",
       "booking_notes": "Late check-in"
     }
   }
   ```
4. Frontend highlights missing fields, pre-fills what it can.
5. User fills in missing fields; frontend sends updated data.
6. Backend returns `"awaiting_confirmation"`; frontend enables “OK” button.
7. User clicks “OK”; frontend sends confirmation.
8. Backend returns `"executed"`; frontend shows success.

---

## Architectural Principles
- **No LLM calls outside `llm_utils.py`.**
- **All parameter extraction and execution intent detection are centralized.**
- **All tools must return structured responses as shown above.**
- **This protocol is mandatory for all new and refactored tools.**

---

## Helper Functions per Step

Each step in the protocol may use one or more helper functions. This modular approach ensures clarity, maintainability, and extensibility.

### 1. Extraction Helpers
- **Always:** `llm_utils.unified_parser` for parameter extraction.
- **Optionally:** Additional normalization helpers (e.g., `normalize_date`).
- **Purpose:** Extract and normalize all required parameters.

### 2. Validation Helpers
- **Examples:**
  - `validate_booking_parameters` (checks guest, room type, etc.)
  - `read_availability` (checks room availability for requested dates/rooms)
  - Other business rule helpers (e.g., policy checks, payment checks)
- **Purpose:** Each helper checks a specific prerequisite or business rule. The tool aggregates the results and returns a structured validation response.
- **IMPORTANT:** Validation must return its results to the orchestrator/user before any execution occurs. No business logic should be executed until validation passes and user confirmation is received.

### 3. Execution Helpers
- **Always:** `llm_utils.unified_execution_detector` for confirmation intent.
- **Only after confirmation:** Business logic helpers (e.g., `create_booking`).
- **Purpose:** Only execute business logic if all validation passes and user confirms.

---

## Refined Example: Booking Tool (Pseudocode)
```python
def create_booking_tool(user_input, dialog_history, user_confirmation=None):
    # Step 1: Extraction
    extracted = unified_parser(user_input, dialog_history, param_list, instruction)
    missing = [k for k, v in extracted.items() if not v and k != "booking_notes"]
    if missing:
        return {"status": "incomplete", "missing": missing, "extracted": extracted}

    # Step 2: Validation (multiple helpers)
    issues = []
    if not validate_booking_parameters(extracted, dbs, date_context):
        issues.append("Booking parameters invalid")
    if not read_availability(extracted['room_type'], extracted['check_in_date'], extracted['rooms_booked']):
        issues.append("Room not available for requested date")
    # ...add more checks as needed...
    if issues:
        return {"status": "validation_failed", "issues": issues, "extracted": extracted}

    # Step 3: Execution (only after confirmation)
    if not user_confirmation or unified_execution_detector(user_confirmation) != "execute":
        return {"status": "awaiting_confirmation", "extracted": extracted}
    result = create_booking(**extracted)
    return {"status": "executed", "result": result}
```

---

## Key Principles
- **Each step can use multiple helpers.**
- **Validation is modular:** Each business rule or prerequisite is a separate helper function.
- **No circular logic:** Validation helpers only check, never execute. Execution helpers only run if all validation passes and user confirms.
- **Tool is the orchestrator of its own stepwise logic.**
- **No business logic is executed until validation passes and user confirmation is received.**

---

## Summary Table
| Status                | Orchestrator Action                                 |
|-----------------------|-----------------------------------------------------|
| incomplete            | Prompt user for missing/ambiguous parameters        |
| awaiting_confirmation | Prompt user for confirmation to proceed             |
| validation_failed     | Inform user of business rule issues, prompt to fix  |
| executed              | Show booking result/confirmation                    |

---

**This protocol is the gold standard for all LEON PMS tools.** 