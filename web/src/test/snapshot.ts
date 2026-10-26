// Original synthetic producer example, mirrored inside the frontend Docker build context.
import example from './fixtures/tendercite-capability-v1.json';

export const snapshotExample = example;
export const snapshotJson = JSON.stringify(example, null, 2);
export const snapshotPeriod = { ...example.assessment_period };
