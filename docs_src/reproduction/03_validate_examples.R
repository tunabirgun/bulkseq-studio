library(jsonlite)

args <- commandArgs(trailingOnly = TRUE)
model_path <- if (length(args)) args[1] else "docs_src/model-examples.json"
learning_path <- if (length(args) > 1) args[2] else "docs_src/learning-examples.json"
model <- jsonlite::fromJSON(model_path, simplifyVector = FALSE)
data <- jsonlite::fromJSON(learning_path, simplifyVector = FALSE)
matrix_rows <- function(rows) do.call(rbind, lapply(rows, unlist))
equal <- function(actual, expected, name) {
  if (!isTRUE(all.equal(unname(actual), unname(expected), tolerance = 3e-8, check.attributes = FALSE))) {
    stop(name, " disagrees with the independent calculation", call. = FALSE)
  }
}

counts <- matrix_rows(model$counts)
samples <- unlist(model$samples)
genes <- unlist(model$genes)
condition <- sub("[0-9]+$", "", samples)
required <- min(table(condition))
stopifnot(nrow(counts) == length(genes), ncol(counts) == length(samples),
          all(is.finite(counts)), all(counts >= 0), all(counts == floor(counts)))
equal(data$filtering$required_samples, as.numeric(required), "Filter sample requirement")
equal(matrix_rows(data$filtering$counts), counts, "Filter input counts")
equal(unlist(model$expression_genes), genes[rowSums(counts >= 10) >= required], "DESeq2 prefilter")
for (engine in names(model$models)) {
  rows <- model$models[[engine]]
  eligible <- vapply(rows, function(row) !is.null(row$padj), logical(1))
  p <- vapply(rows[eligible], function(row) row$pvalue, numeric(1))
  q <- vapply(rows[eligible], function(row) row$padj, numeric(1))
  stopifnot(all(is.finite(p)), all(p >= 0 & p <= 1), all(q >= 0 & q <= 1))
  equal(q, p.adjust(p, method = "BH"), paste(engine, "BH adjustment"))
}

normal <- matrix_rows(data$normalization$counts)
geomeans <- exp(rowMeans(log(normal)))
factors <- exp(apply(log(normal / geomeans), 2, median))
equal(unlist(data$normalization$size_factors), factors, "Median-ratio factors")
totals <- colSums(normal)
equal(unlist(data$normalization$total_factors), totals / exp(mean(log(totals))), "Library-total factors")

expression <- matrix_rows(model$expression)
rownames(expression) <- unlist(model$expression_genes)
for (key in names(data$pca)) {
  state <- data$pca[[key]]
  chosen <- order(apply(expression, 1, var), decreasing = TRUE)[seq_len(state$count)]
  equal(unlist(state$genes), rownames(expression)[chosen], "PCA feature selection")
  fit <- prcomp(t(expression[chosen, ]), center = TRUE, scale. = FALSE)
  equal(unlist(state$percent), 100 * fit$sdev[1:2]^2 / sum(fit$sdev^2), "PCA variance")
  scores <- matrix_rows(state$scores)
  equal(tcrossprod(scores), tcrossprod(fit$x[, 1:2]), "PCA sample geometry")
}

heat <- data$heatmap
values <- expression[unlist(heat$genes), ]
z <- t(scale(t(values)))
equal(matrix_rows(heat$values), values, "Heatmap transformed values")
equal(matrix_rows(heat$z), z, "Heatmap sample-SD row scaling")
tree_distances <- function(rows) {
  tree <- matrix_rows(rows)
  n <- nrow(tree) + 1
  members <- lapply(seq_len(n), identity)
  distances <- matrix(0, n, n)
  for (i in seq_len(nrow(tree))) {
    a <- members[[tree[i, 1] + 1]]
    b <- members[[tree[i, 2] + 1]]
    distances[a, b] <- tree[i, 3]
    distances[b, a] <- tree[i, 3]
    members[[n + i]] <- c(a, b)
  }
  distances
}
for (key in names(heat$states)) {
  settings <- as.numeric(strsplit(key, ":", fixed = TRUE)[[1]])
  n <- settings[1]
  cap <- settings[2]
  state <- heat$states[[key]]
  clipped <- z[seq_len(n), ]
  clipped[clipped > cap] <- cap
  clipped[clipped < -cap] <- -cap
  equal(state$clipped_cells, sum(abs(z[seq_len(n), ]) > cap), "Heatmap clipped count")
  for (axis in c("row", "column")) {
    input <- if (axis == "row") clipped else t(clipped)
    tree <- hclust(dist(input, method = "euclidean"), method = "ward.D2")
    equal(tree_distances(state[[paste0(axis, "_tree")]]), as.matrix(cophenetic(tree)), paste("Ward clustering", key, axis))
    equal(sort(unlist(state[[paste0(axis, "_order")]])), seq_len(nrow(input)) - 1, "Heatmap leaf permutation")
  }
}

for (state in data$design) {
  matrix <- matrix_rows(state$matrix)
  rank <- qr(matrix)$rank
  equal(state$rank, rank, "Design rank")
  equal(state$residual_df, nrow(matrix) - rank, "Design residual degrees of freedom")
  stopifnot(state$estimable == (rank == ncol(matrix)))
}
for (rows in data$enrichment) {
  p <- vapply(rows, function(row) phyper(row$overlap - 1, row$term_size,
    row$universe - row$term_size, row$foreground, lower.tail = FALSE), numeric(1))
  equal(vapply(rows, function(row) row$pvalue, numeric(1)), p, "Hypergeometric enrichment")
  equal(vapply(rows, function(row) row$padj, numeric(1)), p.adjust(p, "BH"), "Enrichment BH family")
}
cat("Independent fixture checks passed: counts, BH, normalization, PCA, heatmap, design and enrichment.\n")
print(sessionInfo())
