import streamlit as st
import plotly as py
import plotly.figure_factory as ff
from requests import post
from time import time
from random import sample
from msmarco import load_msmarco_queries, load_msmarco_qrels, extract_querie_relevance
from pandas import DataFrame


QUERIES_FILE_PATH = "msmarco/sample/msmarco-doctrain-queries.tsv.gz"
RELEVANCE_FILE_PATH = "msmarco/sample/msmarco-doctrain-qrels.tsv.gz"
RANK_PROFILE_MAP = {"BM25": "bm25", "Native Rank": "default"}
GRAMMAR_OPERATOR_MAP = {"AND": False, "OR": True}


def main():
    vespa_url = st.sidebar.text_input("Vespa url", "http://localhost")
    vespa_port = st.sidebar.text_input("Vespa port", 8080)

    page = st.sidebar.selectbox(
        "Choose a page", ["Simple query", "Ranking function comparison"]
    )

    if page == "Simple query":
        page_simple_query_page(vespa_url=vespa_url, vespa_port=vespa_port)
    elif page == "Ranking function comparison":
        page_ranking_function_comparison(vespa_url=vespa_url, vespa_port=vespa_port)


def page_ranking_function_comparison(vespa_url, vespa_port):
    rank_profile_1 = st.sidebar.selectbox(
        "Ranking 1: rank profile", ("BM25", "Native Rank")
    )
    grammar_operator_1 = st.sidebar.selectbox("Ranking 1: Grammar", ("AND", "OR"))
    rank_profile_2 = st.sidebar.selectbox(
        "Ranking 2: rank profile", ("BM25", "Native Rank")
    )
    grammar_operator_2 = st.sidebar.selectbox("Ranking 2: Grammar", ("AND", "OR"))

    hits = st.text_input("Number of hits to evaluate per query", "10")

    if st.button("Evaluate"):
        query_relevance = sample_query_relevance_data(number_queries=100)

        records_1, aggregate_metrics_1, position_freq_1 = evaluate(
            query_relevance=query_relevance,
            rank_profile=RANK_PROFILE_MAP[rank_profile_1],
            grammar_any=GRAMMAR_OPERATOR_MAP[grammar_operator_1],
            vespa_url=vespa_url,
            vespa_port=vespa_port,
            hits=int(hits),
        )

        records_2, aggregate_metrics_2, position_freq_2 = evaluate(
            query_relevance=query_relevance,
            rank_profile=RANK_PROFILE_MAP[rank_profile_2],
            grammar_any=GRAMMAR_OPERATOR_MAP[grammar_operator_2],
            vespa_url=vespa_url,
            vespa_port=vespa_port,
            hits=int(hits),
        )

        z = [[x, y] for x, y in zip(position_freq_1, position_freq_2)]
        z_text = z
        x = [
            rank_profile_1 + ", " + grammar_operator_1,
            rank_profile_2 + ", " + grammar_operator_2,
        ]
        y = [str(x + 1) for x in range(int(hits))]

        fig = ff.create_annotated_heatmap(
            z, x=x, y=y, annotation_text=z_text, colorscale=py.colors.diverging.RdYlGn
        )
        fig.update_layout(
            xaxis_title_text="Rank profile",  # xaxis label
            yaxis_title_text="Position",  # yaxis label
        )
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig)

        st.write(DataFrame(data=[aggregate_metrics_1, aggregate_metrics_2]))


def page_simple_query_page(vespa_url, vespa_port):
    query = st.text_input("Query", "")

    rank_profile = st.selectbox("Select desired rank profile", ("BM25", "Native Rank"))

    grammar_operator = st.selectbox(
        "Which grammar operator to apply fo query tokens", ("AND", "OR")
    )

    if query != "":
        search_results = vespa_search(
            query=query,
            rank_profile=RANK_PROFILE_MAP[rank_profile],
            vespa_url=vespa_url,
            vespa_port=vespa_port,
            grammar_any=GRAMMAR_OPERATOR_MAP[grammar_operator],
        )
        output_format = st.radio(
            "Select output format", ("parsed vespa results", "raw vespa results")
        )
        if output_format == "raw vespa results":
            st.markdown("## Showing raw results")
            st.write(search_results)
        elif output_format == "parsed vespa results":
            st.markdown("## Showing parsed results")
            st.markdown("### Click to see more")
            results_title = {
                hit["fields"]["title"]: {
                    "url": hit["fields"]["url"],
                    "body": hit["fields"]["body"],
                    "relevance": hit["relevance"],
                    "id": hit["id"],
                }
                for hit in search_results["root"]["children"]
            }
            for title in results_title:
                if st.checkbox(title):
                    st.markdown(
                        "* relevance: {}".format(results_title[title]["relevance"])
                    )
                    st.markdown("* docid: {}".format(results_title[title]["id"]))
                    st.markdown("* url: {}".format(results_title[title]["url"]))
                    st.markdown("* text:")
                    st.write(results_title[title]["body"])


@st.cache()
def sample_query_relevance_data(number_queries):
    queries = load_msmarco_queries(queries_file_path=QUERIES_FILE_PATH)
    qrels = load_msmarco_qrels(relevance_file_path=RELEVANCE_FILE_PATH)
    sample_qrels = {k: qrels[k] for k in sample(list(qrels), number_queries)}
    query_relevance = extract_querie_relevance(sample_qrels, queries)
    return query_relevance


def parse_vespa_json(data):
    ranking = []
    if "children" in data["root"]:
        ranking = [
            (hit["fields"]["id"], hit["relevance"])
            for hit in data["root"]["children"]
            if "fields" in hit
        ]
    return ranking


@st.cache(persist=True)
def evaluate(query_relevance, rank_profile, grammar_any, vespa_url, vespa_port, hits):
    if grammar_any:
        grammar_name = "OR"
    else:
        grammar_name = "AND"
    number_queries = 0
    total_rr = 0
    start_time = time()
    records = []
    position_count = [0] * hits
    for qid, (query, relevant_id) in query_relevance.items():
        rr = 0
        vespa_result = vespa_search(
            query=query,
            rank_profile=rank_profile,
            vespa_url=vespa_url,
            vespa_port=vespa_port,
            grammar_any=grammar_any,
            hits=hits,
            offset=0,
            summary="minimal",
        )
        ranking = parse_vespa_json(data=vespa_result)
        for rank, hit in enumerate(ranking):
            if hit[0] == relevant_id:
                rr = 1 / (rank + 1)
                position_count[rank] += 1
        records.append({"qid": qid, "rr": rr})
        total_rr += rr
        number_queries += 1
    execution_time = time() - start_time
    aggregate_metrics = {
        "rank_name": rank_profile + ", " + grammar_name,
        "number_queries": number_queries,
        "qps": number_queries / execution_time,
        "mrr": total_rr / number_queries,
    }
    position_freq = [count / number_queries for count in position_count]
    return records, aggregate_metrics, position_freq


@st.cache()
def vespa_search(
    query,
    rank_profile,
    vespa_url,
    vespa_port,
    grammar_any=False,
    hits=10,
    offset=0,
    summary=None,
):

    if grammar_any:
        yql = (
            'select * from sources * where ([{"grammar": "any"}]userInput(@userQuery));'
        )
    else:
        yql = "select * from sources * where (userInput(@userQuery));"

    body = {
        "yql": yql,
        "userQuery": query,
        "hits": hits,
        "offset": offset,
        "ranking": rank_profile,
        "timeout": 1,
        "presentation.format": "json",
    }
    if summary == "minimal":
        body.update({"summary": "minimal"})
    r = post(vespa_url + ":" + vespa_port + "/search/", json=body)
    return r.json()


if __name__ == "__main__":
    main()
