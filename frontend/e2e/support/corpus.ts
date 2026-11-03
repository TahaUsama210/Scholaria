import type { PdfPage } from "./pdf";

export interface FixtureDoc {
  title: string;
  courseCode: string;
  filename: string;
  pages: PdfPage[];
}

/** Short lecture-note fixtures mirroring the demo corpus courses. */
export const ML_NOTES: FixtureDoc = {
  title: "CS480 Lecture 6 - Neural Networks",
  courseCode: "CS480",
  filename: "cs480-lecture6-neural-networks.pdf",
  pages: [
    {
      paragraphs: [
        "Backpropagation computes the gradient of the loss with respect to every weight in the network by applying the chain rule layer by layer, starting at the output and moving back towards the input.",
        "The forward pass stores each layer's activations. The backward pass reuses them, so computing all gradients costs about the same as one extra forward pass. This is what makes training deep networks tractable.",
      ],
    },
    {
      paragraphs: [
        "Gradient descent updates each parameter in the direction of the negative gradient of the loss. The learning rate controls the step size: too large and training diverges, too small and convergence is slow.",
        "Stochastic gradient descent estimates the gradient from a mini-batch, trading noisier updates for far cheaper iterations.",
      ],
    },
    {
      paragraphs: [
        "Overfitting occurs when a model memorises noise in the training set. The validation loss rises while the training loss keeps falling.",
        "Common remedies are L2 weight decay, dropout, early stopping and data augmentation.",
      ],
    },
  ],
};

export const DB_NOTES: FixtureDoc = {
  title: "CS348 Normal Forms",
  courseCode: "CS348",
  filename: "cs348-normal-forms.pdf",
  pages: [
    {
      paragraphs: [
        "A relation is in second normal form (2NF) when it is in first normal form and every non-key attribute depends on the whole of every candidate key, not just part of a composite key.",
      ],
    },
    {
      paragraphs: [
        "Third normal form (3NF) additionally forbids transitive dependencies: a non-key attribute must not depend on another non-key attribute. Decomposing a relation into 3NF removes update anomalies while preserving functional dependencies.",
      ],
    },
  ],
};

export const OS_NOTES: FixtureDoc = {
  title: "CS350 Virtual Memory",
  courseCode: "CS350",
  filename: "cs350-virtual-memory.pdf",
  pages: [
    {
      paragraphs: [
        "A page fault occurs when a process accesses a virtual page that is not resident in physical memory. The MMU traps to the kernel, which finds a free frame or evicts a victim page, reads the page from disk, updates the page table and restarts the faulting instruction.",
      ],
    },
    {
      paragraphs: [
        "LRU evicts the page unused for the longest time. The clock algorithm approximates LRU cheaply with a reference bit per frame.",
      ],
    },
  ],
};

export const NET_NOTES: FixtureDoc = {
  title: "CS456 TCP Congestion Control",
  courseCode: "CS456",
  filename: "cs456-tcp-congestion-control.pdf",
  pages: [
    {
      paragraphs: [
        "TCP congestion control grows the congestion window by one segment per round trip during congestion avoidance and halves it when packet loss is detected through triple duplicate ACKs.",
        "Slow start doubles the window every round trip until it reaches the slow-start threshold. A retransmission timeout resets the window to one segment.",
      ],
    },
  ],
};
