// face_probe: Apple Vision detections for video-framing.
//
// Usage: face_probe in.json out.json
//   in.json  - a JSON list of image paths
//   out.json - one object per image, in order:
//     {"face": Box|null, "eyes": [x, y]|null, "head": Box|null, "hands": [Box...], "joints": {name: [x, y]}}
//
// Coordinates are normalised 0..1 with the origin at the top left (Vision's bottom-left origin is
// flipped). A Box is [left, top, right, bottom]. With several faces the largest is kept; the body pose
// kept is the one whose neck or nose lies nearest that face.
import Foundation
import ImageIO
import Vision

let minConfidence: Float = 0.3

let jointNames: [(VNHumanBodyPoseObservation.JointName, String)] = [
    (.nose, "nose"), (.neck, "neck"),
    (.leftShoulder, "left_shoulder"), (.rightShoulder, "right_shoulder"),
    (.leftElbow, "left_elbow"), (.rightElbow, "right_elbow"),
    (.leftWrist, "left_wrist"), (.rightWrist, "right_wrist"),
    (.leftHip, "left_hip"), (.rightHip, "right_hip"),
    (.leftKnee, "left_knee"), (.rightKnee, "right_knee"),
]

func topLeft(_ p: CGPoint) -> [Double] { [Double(p.x), 1.0 - Double(p.y)] }

func box(_ r: CGRect) -> [Double] {
    [Double(r.minX), 1.0 - Double(r.maxY), Double(r.maxX), 1.0 - Double(r.minY)]
}

func clamp(_ b: [Double]) -> [Double] { b.map { min(max($0, 0.0), 1.0) } }

/// The head: the face box (brows to chin) widened for ears and raised for the top of the head.
func headBox(_ f: CGRect) -> [Double] {
    let b = box(f)
    let w = b[2] - b[0], h = b[3] - b[1]
    return clamp([b[0] - 0.15 * w, b[1] - 0.5 * h, b[2] + 0.15 * w, b[3] + 0.05 * h])
}

func eyeCentre(_ face: VNFaceObservation) -> [Double]? {
    guard let lm = face.landmarks else { return nil }
    let bb = face.boundingBox
    var xs: [Double] = [], ys: [Double] = []
    for region in [lm.leftEye, lm.rightEye] {
        guard let region = region else { continue }
        for p in region.normalizedPoints {
            xs.append(Double(bb.minX + p.x * bb.width))
            ys.append(Double(bb.minY + p.y * bb.height))
        }
    }
    guard !xs.isEmpty else { return nil }
    let x = xs.reduce(0, +) / Double(xs.count), y = ys.reduce(0, +) / Double(ys.count)
    return [x, 1.0 - y]
}

func probe(_ path: String) -> [String: Any] {
    var result: [String: Any] = ["face": NSNull(), "eyes": NSNull(), "head": NSNull(),
                                 "hands": [[Double]](), "joints": [String: [Double]]()]
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let image = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        FileHandle.standardError.write("face_probe: cannot read \(path)\n".data(using: .utf8)!)
        return result
    }
    let faceReq = VNDetectFaceLandmarksRequest()
    let bodyReq = VNDetectHumanBodyPoseRequest()
    let handReq = VNDetectHumanHandPoseRequest()
    handReq.maximumHandCount = 2
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    do {
        try handler.perform([faceReq, bodyReq, handReq])
    } catch {
        FileHandle.standardError.write("face_probe: \(path): \(error)\n".data(using: .utf8)!)
        return result
    }

    var faceCentre: CGPoint? = nil
    let faces = faceReq.results ?? []
    if let face = faces.max(by: { $0.boundingBox.width * $0.boundingBox.height
                                  < $1.boundingBox.width * $1.boundingBox.height }) {
        result["face"] = box(face.boundingBox)
        result["head"] = headBox(face.boundingBox)
        if let e = eyeCentre(face) { result["eyes"] = e }
        faceCentre = CGPoint(x: face.boundingBox.midX, y: face.boundingBox.midY)
    }

    var bestJoints: [String: [Double]] = [:]
    var bestDist = Double.infinity
    for body in bodyReq.results ?? [] {
        var joints: [String: [Double]] = [:]
        var anchor: CGPoint? = nil
        for (jn, name) in jointNames {
            guard let p = try? body.recognizedPoint(jn), p.confidence >= minConfidence else { continue }
            joints[name] = topLeft(p.location)
            if jn == .neck || (jn == .nose && anchor == nil) { anchor = p.location }
        }
        if joints.isEmpty { continue }
        var d = 0.0
        if let fc = faceCentre, let a = anchor {
            d = Double(hypot(fc.x - a.x, fc.y - a.y))
        } else if faceCentre != nil {
            d = 10.0
        }
        if d < bestDist { bestDist = d; bestJoints = joints }
    }
    result["joints"] = bestJoints

    var hands: [[Double]] = []
    for hand in handReq.results ?? [] {
        guard let pts = try? hand.recognizedPoints(.all) else { continue }
        let good = pts.values.filter { $0.confidence >= minConfidence }
        if good.isEmpty { continue }
        let xs = good.map { Double($0.location.x) }, ys = good.map { 1.0 - Double($0.location.y) }
        hands.append(clamp([xs.min()!, ys.min()!, xs.max()!, ys.max()!]))
    }
    result["hands"] = hands
    return result
}

let args = CommandLine.arguments
guard args.count == 3 else {
    FileHandle.standardError.write("usage: face_probe in.json out.json\n".data(using: .utf8)!)
    exit(2)
}
do {
    let data = try Data(contentsOf: URL(fileURLWithPath: args[1]))
    guard let paths = try JSONSerialization.jsonObject(with: data) as? [String] else {
        FileHandle.standardError.write("face_probe: in.json must be a list of paths\n".data(using: .utf8)!)
        exit(2)
    }
    let out = paths.map { probe($0) }
    let json = try JSONSerialization.data(withJSONObject: out, options: [])
    try json.write(to: URL(fileURLWithPath: args[2]))
} catch {
    FileHandle.standardError.write("face_probe: \(error)\n".data(using: .utf8)!)
    exit(1)
}
