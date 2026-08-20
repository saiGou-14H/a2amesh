-- a2am.claim_auth_request.v1
--
-- KEYS[1] = auth replay STRING generated exclusively by RedisKeyBuilder.
-- ARGV[1] = lowercase SHA-256 auth proof digest.
-- ARGV[2] = positive JSON-safe config generation decimal.
-- ARGV[3] = positive JSON-safe replay TTL milliseconds decimal.
--
-- Return: 1=CLAIMED, 0=REPLAYED, -1=CORRUPT_REPLAY_KEY, -2=INVALID_ABI.

local MAX_SAFE_INTEGER = '9007199254740991'

local function is_positive_safe_integer(value)
  if string.match(value, '^[1-9][0-9]*$') == nil then
    return false
  end
  if #value < #MAX_SAFE_INTEGER then
    return true
  end
  return #value == #MAX_SAFE_INTEGER and value <= MAX_SAFE_INTEGER
end

if #KEYS ~= 1 or #ARGV ~= 3 then
  return -2
end
if #ARGV[1] ~= 64 or string.match(ARGV[1], '^[0-9a-f]+$') == nil then
  return -2
end
if not is_positive_safe_integer(ARGV[2]) or not is_positive_safe_integer(ARGV[3]) then
  return -2
end

local key_type = redis.call('TYPE', KEYS[1])['ok']
if key_type ~= 'none' and key_type ~= 'string' then
  return -1
end
if key_type == 'string' then
  return 0
end

local value = 'v1:' .. ARGV[1] .. ':' .. ARGV[2]
local result = redis.call('SET', KEYS[1], value, 'NX', 'PX', ARGV[3])
if result['ok'] == 'OK' then
  return 1
end

-- The script is atomic. A nil SET result can only be an unexpected substrate
-- condition; fail closed without exposing data or modifying another key.
return -2
