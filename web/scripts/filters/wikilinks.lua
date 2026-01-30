local wikilinks = {}

local function has_class(el, name)
  for _, class in ipairs(el.classes or {}) do
    if class == name then
      return true
    end
  end
  return false
end

local function normalize(target)
  target = target:gsub("^%s+", ""):gsub("%s+$", "")
  if target:sub(1, 2) == "[[" and target:sub(-2) == "]]" then
    target = target:sub(3, -3)
  end
  target = target:gsub("|.*$", "")
  target = target:gsub("#.*$", "")
  target = target:gsub("%.md$", "")
  target = target:gsub("%s+", " ")
  return target:lower()
end

function Meta(meta)
  if meta.wikilinks then
    for k, v in pairs(meta.wikilinks) do
      wikilinks[tostring(k):lower()] = pandoc.utils.stringify(v)
    end
  end
end

function Link(el)
  if not has_class(el, "wikilink") then
    return nil
  end

  local key = normalize(el.target or "")
  local resolved = wikilinks[key]
  if resolved then
    el.target = resolved
    return el
  end

  return el.content
end
