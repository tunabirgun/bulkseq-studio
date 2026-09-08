# One ORA scope rule for every consumer of enrichment_objects.rds. The three GO BP
# ORAs (combined, up, down) are separate BH families, so a figure and an export that
# picked different scopes would describe different tests under the same term names.
# Rule: use the combined-foreground ORA; only when it returned no adjusted-significant
# term and a directional ORA did, use the direction with more terms (up on a tie).
# GSEA is never returned here -- it is a different test and must be labelled as such.
select_enrichment_scope <- function(obj) {
  n <- function(x) if (is.null(x)) 0L else
    tryCatch(as.integer(nrow(as.data.frame(x))), error = function(e) 0L)
  combined_n <- n(obj[["ego_all"]])
  up_n <- n(obj[["ego_up"]])
  down_n <- n(obj[["ego_down"]])
  object <- obj[["ego_all"]]
  scope <- "combined-foreground"
  if (combined_n == 0L && max(up_n, down_n) > 0L) {
    if (up_n >= down_n) {
      object <- obj[["ego_up"]]
      scope <- "up-regulated"
    } else {
      object <- obj[["ego_down"]]
      scope <- "down-regulated"
    }
  }
  list(object = object, scope = scope, selected_n = n(object),
       combined_n = combined_n, up_n = up_n, down_n = down_n)
}
