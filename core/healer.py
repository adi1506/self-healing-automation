from difflib import SequenceMatcher
from core.scanner import Scanner
from core.excel_manager import ExcelManager
from core.ai_matcher import AIMatcher


class Healer:
    def __init__(self, ai_api_key: str = ""):
        self.scanner = Scanner()
        self.ai_matcher = AIMatcher(api_key=ai_api_key)

    async def heal(self, url: str, excel_manager: ExcelManager) -> dict:
        """
        Compare current page state against stored element map and heal broken selectors.
        Returns a report dict with counts and change details.
        """
        stored_elements = excel_manager.read_element_map(url)
        current_elements = await self.scanner.scan(url)

        matched_stored = set()
        matched_current = set()
        changes = []
        healed_elements = []

        # Phase 1: Try to match stored elements to current elements
        for s_idx, stored in enumerate(stored_elements):
            match_result = self._try_match(stored, current_elements, matched_current)

            if match_result["status"] == "UNCHANGED":
                matched_stored.add(s_idx)
                matched_current.add(match_result["current_index"])
                healed_elements.append(self._merge_element(stored, current_elements[match_result["current_index"]], "UNCHANGED", "", ""))

            elif match_result["status"] == "CHANGED":
                matched_stored.add(s_idx)
                matched_current.add(match_result["current_index"])
                current = current_elements[match_result["current_index"]]
                change_details = self._compute_change_details(stored, current)
                changes.append({
                    "element_name": current.get("element_name", stored["element_name"]),
                    "change_details": change_details,
                    "healed_by": match_result["healed_by"],
                })
                healed_elements.append(self._merge_element(stored, current, "CHANGED", change_details, match_result["healed_by"]))

            elif match_result["status"] == "UNRESOLVED":
                matched_stored.add(s_idx)
                changes.append({
                    "element_name": stored["element_name"],
                    "change_details": "All selectors failed, AI unavailable",
                    "healed_by": "",
                })
                stored_copy = dict(stored)
                stored_copy["status"] = "UNRESOLVED"
                stored_copy["change_details"] = "All selectors failed, AI unavailable"
                stored_copy["healed_by"] = ""
                healed_elements.append(stored_copy)

            else:
                # REMOVED
                matched_stored.add(s_idx)
                changes.append({
                    "element_name": stored["element_name"],
                    "change_details": "Element no longer found on page",
                    "healed_by": "",
                })
                stored_copy = dict(stored)
                stored_copy["status"] = "REMOVED"
                stored_copy["change_details"] = "Element no longer found on page"
                stored_copy["healed_by"] = ""
                healed_elements.append(stored_copy)

        # Phase 2: Detect NEW elements
        new_count = 0
        for c_idx, current in enumerate(current_elements):
            if c_idx not in matched_current:
                new_count += 1
                current_copy = dict(current)
                current_copy["sno"] = len(healed_elements) + 1
                current_copy["status"] = "NEW"
                current_copy["change_details"] = "New element detected"
                current_copy["healed_by"] = ""
                healed_elements.append(current_copy)
                changes.append({
                    "element_name": current["element_name"],
                    "change_details": "NEW ELEMENT",
                    "healed_by": "",
                })

        # Renumber S.No
        for i, elem in enumerate(healed_elements):
            elem["sno"] = i + 1

        # Save updated element map
        excel_manager.save_element_map(url, healed_elements)

        # Compute counts
        unchanged = sum(1 for e in healed_elements if e["status"] == "UNCHANGED")
        changed = sum(1 for e in healed_elements if e["status"] == "CHANGED")
        removed = sum(1 for e in healed_elements if e["status"] == "REMOVED")
        unresolved = sum(1 for e in healed_elements if e["status"] == "UNRESOLVED")

        return {
            "total_elements": len(healed_elements),
            "unchanged": unchanged,
            "changed": changed,
            "new": new_count,
            "removed": removed,
            "unresolved": unresolved,
            "changes": changes,
        }

    def _try_match(self, stored: dict, current_elements: list[dict], already_matched: set) -> dict:
        """
        Try to find the stored element among current elements using the 3-level fallback.
        NOTE: This is NOT async — the matching is done by comparing data, not by using Playwright.
        """
        # Level 1: Direct locator matching
        for c_idx, current in enumerate(current_elements):
            if c_idx in already_matched:
                continue
            if self._locators_match(stored, current):
                if self._attributes_identical(stored, current):
                    return {"status": "UNCHANGED", "current_index": c_idx, "healed_by": ""}
                else:
                    return {"status": "CHANGED", "current_index": c_idx, "healed_by": "Level 1 (selector match)"}

        # Level 1b: Partial locator match
        for c_idx, current in enumerate(current_elements):
            if c_idx in already_matched:
                continue
            matching_locators = self._count_matching_locators(stored, current)
            if matching_locators >= 1:
                return {"status": "CHANGED", "current_index": c_idx, "healed_by": f"Level 1 ({matching_locators} locator(s))"}

        # Level 2: Attribute-based matching
        stored_fp = self._build_fingerprint(stored)
        best_score = 0
        best_idx = -1
        candidates_in_range = []

        for c_idx, current in enumerate(current_elements):
            if c_idx in already_matched:
                continue
            current_fp = self._build_fingerprint(current)
            score = self.calculate_similarity(stored_fp, current_fp)
            if score >= 0.85:
                if score > best_score:
                    best_score = score
                    best_idx = c_idx
            elif score >= 0.6:
                candidates_in_range.append((c_idx, score))

        if best_idx >= 0:
            return {"status": "CHANGED", "current_index": best_idx, "healed_by": f"Level 2 (attribute, {best_score:.0%})"}

        # Level 3: Gemini AI matching
        if self.ai_matcher.is_available():
            # Level 2 gray zone — single candidate, ask Gemini to confirm
            if len(candidates_in_range) == 1:
                c_idx = candidates_in_range[0][0]
                unmatched = [current_elements[c_idx]]
                ai_result = self.ai_matcher.match_element(stored, unmatched)
                if ai_result and ai_result.get("match_index") == 0 and ai_result.get("confidence", 0) >= 0.7:
                    return {"status": "CHANGED", "current_index": c_idx, "healed_by": "Level 3 (Gemini confirmed)"}

            # Full Gemini matching on all unmatched
            unmatched = [
                current_elements[i] for i in range(len(current_elements))
                if i not in already_matched
            ]
            if unmatched:
                ai_result = self.ai_matcher.match_element(stored, unmatched)
                if ai_result and ai_result.get("match_index", -1) >= 0 and ai_result.get("confidence", 0) >= 0.7:
                    unmatched_indices = [i for i in range(len(current_elements)) if i not in already_matched]
                    original_idx = unmatched_indices[ai_result["match_index"]]
                    return {"status": "CHANGED", "current_index": original_idx, "healed_by": f"Level 3 (Gemini, {ai_result['confidence']:.0%})"}

        # If candidates in range but no AI, mark unresolved
        if candidates_in_range and not self.ai_matcher.is_available():
            return {"status": "UNRESOLVED"}

        # Nothing found at all
        return {"status": "REMOVED"}

    def _locators_match(self, stored: dict, current: dict) -> bool:
        """Check if the primary locators match between stored and current element."""
        locator_keys = ["locator_id", "locator_name", "locator_data_testid"]
        for key in locator_keys:
            s_val = stored.get(key, "")
            c_val = current.get(key, "")
            if s_val and c_val and s_val == c_val:
                return True
        return False

    def _count_matching_locators(self, stored: dict, current: dict) -> int:
        """Count how many locators match between stored and current."""
        count = 0
        locator_keys = ["locator_id", "locator_name", "locator_css", "locator_xpath", "locator_data_testid", "locator_label"]
        for key in locator_keys:
            s_val = stored.get(key, "")
            c_val = current.get(key, "")
            if s_val and c_val and s_val == c_val:
                count += 1
        return count

    def _attributes_identical(self, stored: dict, current: dict) -> bool:
        """Check if all attributes are the same."""
        keys = ["element_name", "element_type", "locator_id", "locator_name",
                "locator_css", "locator_xpath", "locator_data_testid", "locator_label",
                "placeholder", "available_options"]
        for key in keys:
            if str(stored.get(key, "")) != str(current.get(key, "")):
                return False
        return True

    def _build_fingerprint(self, elem: dict) -> dict:
        """Build a fingerprint dict for attribute-based matching."""
        return {
            "element_type": elem.get("element_type", ""),
            "label_text": elem.get("locator_label", "") or elem.get("element_name", ""),
            "placeholder": elem.get("placeholder", ""),
        }

    def calculate_similarity(self, fp1: dict, fp2: dict) -> float:
        """Calculate similarity score between two fingerprints (0.0 to 1.0)."""
        scores = []

        # Element type match (exact) — weight: 0.3
        type_score = 1.0 if fp1.get("element_type") == fp2.get("element_type") else 0.0
        scores.append(("type", type_score, 0.3))

        # Label text similarity — weight: 0.4
        label1 = fp1.get("label_text", "").lower()
        label2 = fp2.get("label_text", "").lower()
        label_score = SequenceMatcher(None, label1, label2).ratio() if label1 or label2 else 0.0
        scores.append(("label", label_score, 0.4))

        # Placeholder similarity — weight: 0.3
        ph1 = fp1.get("placeholder", "").lower()
        ph2 = fp2.get("placeholder", "").lower()
        if ph1 or ph2:
            ph_score = SequenceMatcher(None, ph1, ph2).ratio()
        else:
            ph_score = 0.5  # neutral if both empty
        scores.append(("placeholder", ph_score, 0.3))

        total = sum(score * weight for _, score, weight in scores)
        return total

    def _compute_change_details(self, stored: dict, current: dict) -> str:
        """Generate a human-readable change details string."""
        changes = []
        keys_to_compare = {
            "element_name": "Element Name",
            "locator_id": "ID",
            "locator_name": "Name",
            "locator_css": "CSS",
            "locator_xpath": "XPath",
            "locator_data_testid": "Data-TestID",
            "locator_label": "Label",
            "placeholder": "Placeholder",
            "available_options": "Options",
        }
        for key, label in keys_to_compare.items():
            old_val = str(stored.get(key, ""))
            new_val = str(current.get(key, ""))
            if old_val != new_val:
                changes.append(f"{label}: {old_val} -> {new_val}")
        return ", ".join(changes) if changes else ""

    def _merge_element(self, stored: dict, current: dict, status: str, change_details: str, healed_by: str) -> dict:
        """Create the updated element dict by merging stored and current data."""
        merged = dict(current)
        merged["sno"] = stored["sno"]
        merged["status"] = status
        merged["change_details"] = change_details
        merged["healed_by"] = healed_by
        return merged
