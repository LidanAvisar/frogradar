"""Synthesis over many signals: the daily brief and per-competitor battlecards (D53, D54).

Pattern: the synthesis model proposes, code verifies. Every sentence / item cites signal ids; code
drops any item that cites an id outside the exact input set (unknown, not visible, irrelevant, or
another entity's), then a model from another family checks each remaining item against the
summaries of the signals it cites (app.pipeline.synthesis.check). Dropped items are kept in the
stored report with their reason, never shown.
"""
