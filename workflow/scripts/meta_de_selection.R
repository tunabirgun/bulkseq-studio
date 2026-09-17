meta_de_direction <- function(padj, lfc, alpha, lfc_threshold) {
  direction <- rep("n.s.", length(lfc))
  selected <- !is.na(padj) & is.finite(padj) & padj < alpha &
    !is.na(lfc) & is.finite(lfc) & abs(lfc) >= lfc_threshold
  direction[selected & lfc > 0] <- "Up"
  direction[selected & lfc < 0] <- "Down"
  direction
}
