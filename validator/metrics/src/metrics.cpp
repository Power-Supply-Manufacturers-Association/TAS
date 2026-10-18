// SPDX-License-Identifier: MIT
#include "tas_metrics/metrics.hpp"

#include "tas_validator/validator.hpp"

#include <algorithm>
#include <cctype>
#include <fstream>
#include <set>
#include <stdexcept>
#include <utility>

namespace tas::metrics {

namespace {

// Evidence credit (Alf, 2026-10-17): exact part + values read = 1.0; series datasheet = 0.6;
// existence confirmed without values counts for existence only; anything else 0.
double evidence(const json& e) {
    const auto it = e.find("verification");
    if (it == e.end() || !it->is_string()) return 0.0;
    const auto& v = it->get_ref<const std::string&>();
    if (v == "valuesReadFromSource" || v == "partNamed") return 1.0;
    if (v == "seriesConfirmed") return 0.6;
    return 0.0;
}

double existence_evidence(const json& e) {
    const auto it = e.find("verification");
    if (it != e.end() && it->is_string() && it->get_ref<const std::string&>() == "existenceConfirmed")
        return 1.0;
    return evidence(e);
}

constexpr double kDistributor = 0.8;

double source_factor(const json& e) {
    const auto it = e.find("source");
    return (it != e.end() && it->is_string() && it->get_ref<const std::string&>() == "distributor")
               ? kDistributor
               : 1.0;
}

std::vector<std::string> split(const std::string& s, char d) {
    std::vector<std::string> out;
    std::string cur;
    for (char c : s) {
        if (c == d) {
            out.push_back(cur);
            cur.clear();
        } else {
            cur.push_back(c);
        }
    }
    out.push_back(cur);
    return out;
}

std::string drop_brackets(const std::string& s) {
    std::string out;
    int depth = 0;
    for (char c : s) {
        if (c == '[') ++depth;
        else if (c == ']') depth = std::max(depth - 1, 0);
        else if (depth == 0) out.push_back(c);
    }
    return out;
}

std::vector<std::string> segments(const std::string& path) { return split(drop_brackets(path), '.'); }

bool is_empty_value(const json& v) {
    return v.is_null() || (v.is_object() && v.empty()) || (v.is_array() && v.empty());
}

// Nodes at a path; a list met on the way means "any element". `star` lets '*' stand for every
// value of an object.
std::vector<const json*> walk(const json& root, const std::vector<std::string>& segs, bool star) {
    std::vector<const json*> cur{&root};
    for (const auto& seg : segs) {
        std::vector<const json*> nxt;
        for (const json* c : cur) {
            auto visit = [&](const json& it) {
                if (!it.is_object()) return;
                if (star && seg == "*") {
                    for (const auto& kv : it.items()) nxt.push_back(&kv.value());
                    return;
                }
                const auto f = it.find(seg);
                if (f != it.end()) nxt.push_back(&*f);
            };
            if (c->is_array())
                for (const auto& it : *c) visit(it);
            else
                visit(*c);
        }
        cur = std::move(nxt);
        if (cur.empty()) break;
    }
    return cur;
}

bool present_at(const json& di, const std::vector<std::string>& segs) {
    for (const json* v : walk(di, segs, true))
        if (!is_empty_value(*v)) return true;
    return false;
}

// Every scalar at a path, one array level flattened.
std::vector<const json*> values_at(const json& di, const std::vector<std::string>& segs) {
    std::vector<const json*> out;
    for (const json* v : walk(di, segs, false)) {
        if (v->is_array())
            for (const auto& x : *v) out.push_back(&x);
        else
            out.push_back(v);
    }
    return out;
}

bool contains_value(const std::vector<const json*>& vals, const json& want) {
    return std::any_of(vals.begin(), vals.end(), [&](const json* v) { return *v == want; });
}

bool applies(const std::vector<Scorer::Condition>& when, const json& di, const std::string& kind) {
    for (const auto& c : when) {
        if (c.wrap) {
            if (std::none_of(c.allowed.begin(), c.allowed.end(),
                             [&](const json& a) { return a.is_string() && a.get_ref<const std::string&>() == kind; }))
                return false;
            continue;
        }
        const auto vals = values_at(di, c.segs);
        bool hit = false;
        for (const json* v : vals)
            if (std::any_of(c.allowed.begin(), c.allowed.end(), [&](const json& a) { return a == *v; })) {
                hit = true;
                break;
            }
        if (!hit) return false;
    }
    return true;
}

std::vector<Scorer::Condition> parse_when(const json& entry) {
    std::vector<Scorer::Condition> out;
    if (!entry.is_object()) return out;
    const auto w = entry.find("when");
    if (w == entry.end() || w->is_null()) return out;
    if (!w->is_object()) throw std::invalid_argument("metrics spec: 'when' must be an object");
    for (const auto& kv : w->items()) {
        if (!kv.value().is_array())
            throw std::invalid_argument("metrics spec: 'when." + kv.key() + "' must be a list");
        Scorer::Condition c;
        c.wrap = kv.key() == "$wrap";
        if (!c.wrap) c.segs = segments(kv.key());
        c.allowed.assign(kv.value().begin(), kv.value().end());
        out.push_back(std::move(c));
    }
    return out;
}

Scorer::Entry parse_entry(const json& e) {
    Scorer::Entry out;
    std::string path;
    if (e.is_string()) path = e.get<std::string>();
    else if (e.is_object() && e.contains("path") && e["path"].is_string()) path = e["path"].get<std::string>();
    else throw std::invalid_argument("metrics spec: a field/curve entry needs a path: " + e.dump());
    for (const auto& alt : split(path, '|')) {
        out.alt_text.push_back(alt);
        out.alt_segs.push_back(segments(alt));
    }
    out.when = parse_when(e);
    return out;
}

const json* find_object(const json& obj, const char* key) {
    if (!obj.is_object()) return nullptr;
    const auto it = obj.find(key);
    return (it != obj.end() && it->is_object()) ? &*it : nullptr;
}

std::string string_at(const json* obj, const char* key) {
    if (!obj) return {};
    const auto it = obj->find(key);
    return (it != obj->end() && it->is_string()) ? it->get<std::string>() : std::string{};
}

// Does provenance entry `e` claim field `field_rel` (already relative, brackets dropped)?
bool covers(const json& e, const std::string& field_rel) {
    const auto fs = e.find("fields");
    if (fs == e.end() || fs->is_null()) {
        // a whole-record claim counts only when the values were read
        const auto v = e.find("verification");
        return v != e.end() && v->is_string() && v->get_ref<const std::string&>() == "valuesReadFromSource";
    }
    if (!fs->is_array()) return false;
    for (const auto& x : *fs) {
        if (!x.is_string()) continue;
        const std::string r = relative_field(x.get_ref<const std::string&>());
        if (r == field_rel || (field_rel.size() > r.size() && field_rel.compare(0, r.size(), r) == 0 &&
                               field_rel[r.size()] == '.'))
            return true;
    }
    return false;
}

const tas::PartValidator& blade_runner() {
    static const tas::PartValidator v;
    return v;
}

}  // namespace

std::string normalise_pn(const std::string& pn) {
    std::string out;
    out.reserve(pn.size());
    for (unsigned char c : pn) {
        if (std::isspace(c) || c == '-' || c == '_' || c == '/') continue;
        out.push_back(static_cast<char>(std::toupper(c)));
    }
    return out;
}

std::string manufacturer_key(const std::string& name) {
    std::string out;
    out.reserve(name.size());
    for (unsigned char c : name)
        if (std::isalnum(c) || c >= 0x80) out.push_back(static_cast<char>(std::tolower(c)));
    return out;
}

std::string relative_field(const std::string& path) {
    // Brackets and numeric index segments both name an element of a list, and the spec
    // paths address every element: electrical[0].inductance, electrical[].inductance and
    // electrical.0.inductance all mean electrical.inductance.
    std::string p;
    for (const auto& seg : split(drop_brackets(path), '.')) {
        if (!seg.empty() && std::all_of(seg.begin(), seg.end(),
                                        [](char c) { return std::isdigit(static_cast<unsigned char>(c)) != 0; }))
            continue;
        if (!p.empty()) p.push_back('.');
        p += seg;
    }
    for (const char* prefix : {"manufacturerInfo.datasheetInfo.", "datasheetInfo."}) {
        const std::string pre(prefix);
        if (p.compare(0, pre.size(), pre) == 0) return p.substr(pre.size());
    }
    return p;
}

Scorer::Scorer(const json& spec) {
    const json* w = find_object(spec, "weights");
    if (!w) throw std::invalid_argument("metrics spec: no 'weights' object");
    for (const char* k : {"existence", "traced", "fields", "curves", "url", "valid"}) {
        const auto it = w->find(k);
        if (it == w->end() || !it->is_number())
            throw std::invalid_argument(std::string("metrics spec: weight '") + k + "' missing");
        weights_[k] = it->get<double>();
    }
    const json* cats = find_object(spec, "catalogues");
    if (!cats) throw std::invalid_argument("metrics spec: no 'catalogues' object");
    const json* ph_all = find_object(spec, "placeholders");
    for (const auto& kv : cats->items()) {
        const json& cs = kv.value();
        Catalogue c;
        c.file = cs.contains("file") && cs["file"].is_string() ? cs["file"].get<std::string>() : kv.key();
        if (!cs.contains("wrap") || !cs["wrap"].is_array() || cs["wrap"].empty())
            throw std::invalid_argument("metrics spec: catalogue '" + kv.key() + "' has no wrap");
        for (const auto& s : cs["wrap"]) c.wrap.push_back(s.get<std::string>());
        if (!cs.contains("fields") || !cs["fields"].is_array())
            throw std::invalid_argument("metrics spec: catalogue '" + kv.key() + "' has no fields list");
        for (const auto& f : cs["fields"]) c.fields.push_back(parse_entry(f));
        if (cs.contains("curves"))
            for (const auto& f : cs["curves"]) c.curves.push_back(parse_entry(f));
        if (ph_all && ph_all->contains(kv.key())) {
            for (const auto& p : (*ph_all)[kv.key()]) {
                Placeholder ph;
                ph.path = p.at("path").get<std::string>();
                ph.segs = segments(ph.path);
                ph.value = p.at("value");
                ph.when = parse_when(p);
                c.placeholders.push_back(std::move(ph));
            }
        }
        catalogues_.emplace(kv.key(), std::move(c));
    }
    if (const json* cf = find_object(spec, "code_fields"))
        for (const auto& kv : cf->items())
            for (const auto& f : kv.value()) code_fields_[kv.key()].push_back(f.get<std::string>());
}

Scorer::Scorer(const json& spec, const json& aliases) : Scorer(spec) {
    const json* m = aliases.is_object() && aliases.contains("manufacturers") ? &aliases["manufacturers"] : nullptr;
    if (!m || !m->is_array()) throw std::invalid_argument("manufacturer aliases: no 'manufacturers' list");
    auto bind = [&](const std::string& alias, const std::string& canon) {
        const std::string k = manufacturer_key(alias);
        if (k.empty()) throw std::invalid_argument("manufacturer aliases: empty name '" + alias + "'");
        const auto [it, fresh] = aliases_.emplace(k, canon);
        if (!fresh && it->second != canon)
            throw std::invalid_argument("manufacturer aliases: '" + alias + "' maps to both '" + it->second +
                                        "' and '" + canon + "'");
    };
    for (const auto& e : *m) {
        const std::string canon = manufacturer_key(e.at("name").get<std::string>());
        bind(e.at("name").get<std::string>(), canon);
        if (e.contains("aliases"))
            for (const auto& a : e["aliases"]) bind(a.get<std::string>(), canon);
    }
}

std::vector<std::string> Scorer::catalogues() const {
    std::vector<std::string> out;
    for (const auto& kv : catalogues_) out.push_back(kv.first);
    return out;
}

std::optional<PartScore> Scorer::score(const json& record, const std::string& catalogue, bool schema_ok,
                                       double url_alive) const {
    const auto cit = catalogues_.find(catalogue);
    if (cit == catalogues_.end()) throw std::invalid_argument("metrics: unknown catalogue '" + catalogue + "'");
    const Catalogue& cat = cit->second;

    PartScore ps;
    const json* body = &record;
    for (const auto& k : cat.wrap) {
        if (!body->is_object()) return std::nullopt;
        if (k == "*") {
            if (body->size() != 1) return std::nullopt;
            ps.kind = body->begin().key();
            body = &body->begin().value();
            break;
        }
        const auto it = body->find(k);
        if (it == body->end()) return std::nullopt;
        body = &*it;
    }
    if (!body->is_object()) return std::nullopt;

    static const json kEmpty = json::object();
    const json* mi = find_object(*body, "manufacturerInfo");
    const json* dip = mi ? find_object(*mi, "datasheetInfo") : nullptr;
    const json& di = dip ? *dip : kEmpty;

    std::vector<const json*> prov;
    if (const auto p = di.find("provenance"); p != di.end() && p->is_array())
        for (const auto& e : *p)
            if (e.is_object()) prov.push_back(&e);

    // Blade Runner
    double blade_worst = 0.0;
    try {
        for (const auto& f : blade_runner().validate(record).findings) {
            if (f.severity == tas::Severity::Impossible) blade_worst = 1.0;
            else if (f.severity == tas::Severity::Suspicious) blade_worst = std::max(blade_worst, 0.5);
            else continue;
            ps.blade_codes.push_back(f.code);
        }
    } catch (const std::exception& ex) {
        // the record is uninterpretable to the physics checks: it is not valid, and the
        // driver reports the count so the cause gets fixed in the data
        ps.blade_error = ex.what();
        blade_worst = 1.0;
    }

    Checks& ch = ps.checks;
    for (const json* e : prov) ch.existence = std::max(ch.existence, existence_evidence(*e) * source_factor(*e));

    int pts = 0;
    std::size_t n = 0;
    double tr_sum = 0.0;
    std::size_t tr_n = 0;
    for (const auto& f : cat.fields) {
        if (!applies(f.when, di, ps.kind)) continue;
        ++n;
        std::size_t hit = f.alt_segs.size();
        for (std::size_t i = 0; i < f.alt_segs.size(); ++i)
            if (present_at(di, f.alt_segs[i])) {
                hit = i;
                break;
            }
        if (hit == f.alt_segs.size()) continue;
        const std::string& p = f.alt_text[hit];
        const std::string leaf = f.alt_segs[hit].back();
        bool bad = false;
        for (const auto& code : ps.blade_codes) {
            const auto cf = code_fields_.find(code);
            if (cf != code_fields_.end() && std::find(cf->second.begin(), cf->second.end(), leaf) != cf->second.end())
                bad = true;
        }
        for (const auto& ph : cat.placeholders)
            if (ph.path == p && applies(ph.when, di, ps.kind) && contains_value(values_at(di, ph.segs), ph.value))
                bad = true;
        pts += bad ? -1 : 1;
        const std::string field_rel = relative_field(p);
        double best = 0.0;
        for (const json* e : prov)
            if (covers(*e, field_rel)) best = std::max(best, evidence(*e) * source_factor(*e));
        tr_sum += best;
        ++tr_n;
    }
    ch.fields = n ? static_cast<double>(std::max(pts, 0)) / static_cast<double>(n) : 1.0;
    ch.traced = tr_n ? tr_sum / static_cast<double>(tr_n) : 0.0;

    std::size_t cn = 0, cp = 0;
    for (const auto& c : cat.curves) {
        if (!applies(c.when, di, ps.kind)) continue;
        ++cn;
        for (const auto& segs : c.alt_segs)
            if (present_at(di, segs)) {
                ++cp;
                break;
            }
    }
    if (cn) ch.curves = static_cast<double>(cp) / static_cast<double>(cn);

    // datasheetUrl is a manufacturerInfo field; a provenance sourceUrl also counts
    bool has_url = !string_at(mi, "datasheetUrl").empty() || !string_at(&di, "datasheetUrl").empty();
    for (const json* e : prov)
        if (!string_at(e, "sourceUrl").empty()) has_url = true;
    ch.url = has_url ? url_alive : 0.0;
    ch.valid = schema_ok ? 1.0 - blade_worst : 0.0;

    double num = 0.0, den = 0.0;
    auto add = [&](const char* k, double v) {
        num += weights_.at(k) * v;
        den += weights_.at(k);
    };
    add("existence", ch.existence);
    add("traced", ch.traced);
    add("fields", ch.fields);
    if (ch.curves) add("curves", *ch.curves);
    add("url", ch.url);
    add("valid", ch.valid);
    ps.score = den > 0.0 ? num / den : 0.0;

    const json* part = find_object(di, "part");
    std::string pn = string_at(part, "partNumber");
    if (pn.empty()) pn = string_at(mi, "reference");
    ps.identity_pn = normalise_pn(pn);
    ps.identity_mfr = manufacturer_key(string_at(mi, "name"));
    if (const auto a = aliases_.find(ps.identity_mfr); a != aliases_.end()) ps.identity_mfr = a->second;
    return ps;
}

CatalogueTotals Scorer::score_file(const std::string& path, const std::string& catalogue, double url_alive,
                                   const std::string& schema_mask) const {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("metrics: cannot open " + path);
    CatalogueTotals t;
    std::set<std::pair<std::string, std::string>> ids;
    std::string line;
    for (std::size_t i = 0; i < schema_mask.size() && std::getline(in, line); ++i) {
        if (line.find_first_not_of(" \t\r") == std::string::npos) continue;
        const bool ok = schema_mask[i] != '0';
        if (!ok) ++t.schema_failed;
        json rec = json::parse(line);  // a line that is not JSON is a broken catalogue: throw
        const auto ps = score(rec, catalogue, ok, url_alive);
        if (!ps) {
            ++t.malformed;
            continue;
        }
        ++t.live;
        t.sum += ps->score;
        const Checks& c = ps->checks;
        auto acc = [&](const char* k, double v) {
            t.check_sum[k] += v;
            ++t.check_n[k];
        };
        acc("existence", c.existence);
        acc("traced", c.traced);
        acc("fields", c.fields);
        if (c.curves) acc("curves", *c.curves);
        acc("url", c.url);
        acc("valid", c.valid);
        if (!ps->blade_error.empty()) ++t.blade_errors;
        for (const auto& code : ps->blade_codes) ++t.blade_code_counts[code];
        ids.emplace(ps->identity_pn, ps->identity_mfr);
    }
    t.distinct = ids.size();
    return t;
}

std::map<std::string, std::size_t> quarantine_counts(const std::string& path) {
    std::ifstream in(path);
    if (!in) throw std::runtime_error("metrics: cannot open " + path);
    std::map<std::string, std::size_t> out;
    std::string line;
    while (std::getline(in, line)) {
        if (line.find_first_not_of(" \t\r") == std::string::npos) continue;
        const json rec = json::parse(line);
        const auto it = rec.find("_quarantineSource");
        if (it == rec.end() || !it->is_array() || it->empty() || !(*it)[0].is_string()) {
            ++out[""];  // a quarantined row nobody attributed to a catalogue
            continue;
        }
        const std::string s = (*it)[0].get<std::string>();
        ++out[s.substr(0, s.find('.')) + ".ndjson"];
    }
    return out;
}

}  // namespace tas::metrics
