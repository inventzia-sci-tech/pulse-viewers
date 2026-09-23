# SPDX-License-Identifier: AGPL-3.0-or-later OR LicenseRef-Inventzia-Commercial
# Copyright (c) 2013-2026 Magrino Bini, Paola Apruzzese, Inventzia Science and Technology Ltd.
"""The Python side of the contracts a viewer reads.

``run_layout`` mirrors pulse-beacon's ``RunLayout`` (the output directory shape and manifest);
``event_record`` mirrors the recording envelope, whose JSON Schema ships beside it as package data.
Both have Java twins in pulse-beacon and are kept byte-for-byte compatible with them.
"""
