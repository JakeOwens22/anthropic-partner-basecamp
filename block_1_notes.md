SEEN       1. <[!!] BadRequestError: Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', 'message': 'messages.2.content.0: unexpected `tool_use_id` found in `tool_result` blocks: toolu_01R7BRvfXSoXEXxqf78DuqSF. Each `tool_result` block must have a corresponding `tool_use` block in the previous message.'}, 'request_id': 'req_011CfJt94auKi8Z9LYR8YepZ'}
>            -> <which function>
           2. lookup_booking
  "Retrieve a Larkspur reservation from Altura by confirmation code (PNR) and the passenger's last name. Both are required to prevent a lookup on a guessed PNR. Returns fare family, loyalty tier, the segment that needs attention, and any group/partner/minor/SSR flags relevant to scope."
    pnr                          (string, required)
    last_name                    (string, required)         -> lookup_booking
           3. search_alternatives  <-- 6 characters
  'search'
    pnr                          (string, required)         -> search

PREDICTED  The passing of the data from the `lookup_booking` to the `search` tool 