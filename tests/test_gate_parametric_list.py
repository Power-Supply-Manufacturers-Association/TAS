"""A vendor parametric CATEGORY list (TI selectionmodel result-list, addressed by a
numeric category id) may be stored as document evidence; any search-shaped URL,
including one under selectionmodel that carries a typed query, may not."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import ingest_gate as g  # noqa: E402

LIST = ("https://www.ti.com/selectionmodel/api/gpn/result-list?destinationId=668"
        "&destinationType=GPT&mode=parametric&locale=en-US")


def test_ti_category_list_is_not_search_shaped():
    assert not g.search_shaped(LIST)


def test_query_on_a_category_list_is_still_search_shaped():
    assert g.search_shaped(LIST + "&q=TPS22860")


def test_other_selectionmodel_urls_stay_search_shaped():
    assert g.search_shaped("https://www.ti.com/selectionmodel/search?q=TPS22860")
    assert g.search_shaped("http://www.ti.com/selectionmodel/api/gpn/result-list?destinationId=668"
                           "&destinationType=GPT&mode=parametric&locale=en-US")


def test_generic_search_pages_stay_search_shaped():
    assert g.search_shaped("https://datasheetpdf.com/search?q=IKW75N65H5")
    assert g.search_shaped("https://www.example.com/parametric/caps")


def test_unregistered_list_never_names_a_part():
    assert not g.stored_document_names_part(LIST + "&q=TPS22860", "TPS22860")


ECS = "https://ecsxtal.com/parametric-search/"


def test_ecs_parametric_page_is_not_search_shaped():
    assert not g.search_shaped(ECS)


def test_ecs_parametric_page_with_query_stays_search_shaped():
    assert g.search_shaped(ECS + "?q=ECS-MPI4040")
    assert g.search_shaped("http://ecsxtal.com/parametric-search/")
    assert g.search_shaped("https://ecsxtal.com/parametric-search/crystals")


BOURNS = "https://bourns.com/parametric-search?Cat=npInductors"


def test_bourns_inductor_category_page_is_not_search_shaped():
    assert not g.search_shaped(BOURNS)


def test_bourns_category_page_with_query_stays_search_shaped():
    assert g.search_shaped(BOURNS + "&q=SRR1260")
    assert g.search_shaped("https://bourns.com/parametric-search?Cat=npResistors")
